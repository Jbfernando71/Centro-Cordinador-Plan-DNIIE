#!/usr/bin/env python3
"""Consulta el aviso oficial de Simón sin confundirlo con otros ciclones.

Requisitos: beautifulsoup4, playwright y Chromium.
No asigna números de aviso, categoría ni posiciones por estimación.
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

PORTAL = ('https://smn.conagua.gob.mx/es/pronosticos/avisos/'
          'aviso-de-ciclon-tropical-en-el-oceano-pacifico')
WEBAVISO = 'https://smn.conagua.gob.mx/tools/GUI/PortalLaravel/public/WebAviso'
SALIDA = Path(__file__).resolve().parent / 'datos_simon.json'
STORM = re.compile(r'\bSim[oó]n\b', re.I)


def compact(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def get_first(pattern, content):
    m = re.search(pattern, content, re.I | re.S)
    return compact(m.group(1)) if m else None


def get_between(content, start, end):
    m = re.search(start + r'\s*(.+?)\s*(?=' + end + ')', content, re.I | re.S)
    return compact(m.group(1)) if m else None


def classification(text):
    # En el bloque de síntesis se describe el estado actual, mientras que
    # el pronóstico puede hablar de categorías anteriores o futuras.
    early = text[:2800]
    if re.search(r'Sim[oó]n.{0,180}?baja\s+presi[oó]n\s+remanente|'
                 r'baja\s+presi[oó]n\s+remanente.{0,180}?Sim[oó]n', early, re.I):
        return 'Baja presión remanente'
    for pattern, label in [
        (r'hurac[aá]n\s+Sim[oó]n.{0,110}?categor[ií]a\s+([1-5])', 'Huracán categoría {}'),
        (r'Sim[oó]n.{0,100}?hurac[aá]n.{0,65}?categor[ií]a\s+([1-5])', 'Huracán categoría {}'),
    ]:
        m = re.search(pattern, early, re.I | re.S)
        if m:
            return label.format(m.group(1))
    if re.search(r'tormenta\s+tropical\s+Sim[oó]n|Sim[oó]n.{0,90}?tormenta\s+tropical', early, re.I):
        return 'Tormenta tropical'
    if re.search(r'depresi[oó]n\s+tropical\s+Sim[oó]n|Sim[oó]n.{0,90}?depresi[oó]n\s+tropical', early, re.I):
        return 'Depresión tropical'
    return None


def parse_forecast(soup):
    rows = []
    for tbl in soup.find_all('table'):
        heading = compact(tbl.get_text(' ', strip=True))
        if not (re.search(r'D[ií]a\s*/\s*Hora', heading, re.I)
                and re.search(r'Categor[ií]a', heading, re.I)):
            continue
        for tr in tbl.find_all('tr'):
            cells = [compact(c.get_text(' ', strip=True)) for c in tr.find_all(['td', 'th'])]
            if len(cells) >= 6 and re.fullmatch(r'\d{1,2}/\d{1,2}h?', cells[0], re.I):
                rows.append(dict(zip(
                    ('dia_hora', 'lat', 'lon', 'viento', 'categoria', 'ubicacion'),
                    cells[:6],
                )))
        if rows:
            break
    return rows


def map_url(soup, url):
    for img in soup.find_all('img', src=True):
        src = urljoin(url, img['src'])
        alt = compact(img.get('alt'))
        if ('ImgTray' in src or 'trayectoria' in alt.lower()) and src.startswith('https://smn.conagua.gob.mx/'):
            return src
    return None


def parse_notice(html, official_data_url, expected_number):
    soup = BeautifulSoup(html, 'html.parser')
    text = compact(soup.get_text(' ', strip=True))
    if not STORM.search(text):
        raise ValueError('La página elegida no menciona a Simón.')
    number = get_first(r'Oc[eé]ano\s+Pac[ií]fico\s*-\s*No\.\s*Aviso\s*:\s*(\d+)', text)
    if not number:
        raise ValueError('El aviso oficial no contiene el número identificable.')
    if int(number) != expected_number:
        raise ValueError(f'El selector decía Simón {expected_number}, pero el aviso dice {number}.')

    # El mismo aviso debe identificar el fenómeno; no basta con que Simón
    # aparezca incidentalmente en una tabla histórica de otros ciclones.
    header = text[:1600]
    if not STORM.search(header):
        raise ValueError('No se puede confirmar que el encabezado corresponda a Simón.')

    emission = re.search(
        r'Emisi[oó]n\s*:\s*(20\d{2}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})'
        r'\s*(?:horas?)?\s*\(\s*(\d{1,2}:\d{2})\s+horas?\s+GMT',
        text, re.I | re.S,
    )
    if not emission:
        raise ValueError('Fecha y horas de emisión no confirmadas en el aviso.')
    fecha, hora, gmt = emission.groups()
    clasif = classification(header)
    if not clasif:
        raise ValueError('No se reconoció con certeza la clasificación vigente de Simón.')

    coordenadas = get_between(
        text, r'Ubicaci[oó]n\s+del\s+centro\s*\|?',
        r'Distancia\s+al\s+lugar\s+m[aá]s\s+cercano|Desplazamiento\s+actual',
    )
    lat = get_first(r'Latitud\s+Norte\s*:\s*(\d+(?:\.\d+)?)', coordenadas or '')
    lon = get_first(r'Longitud\s+Oeste\s*:\s*(\d+(?:\.\d+)?)', coordenadas or '')
    viento_bloque = get_between(
        text, r'Vientos\s+m[aá]ximos\s*(?:\[km/h\])?\s*\|?',
        r'Presi[oó]n\s+m[ií]nima|Pron[oó]stico\s+de\s+lluvia',
    )
    viento = get_first(r'Sostenidos\s*:\s*(\d{1,3})', viento_bloque or '')
    racha = get_first(r'Rachas\s*:\s*(\d{1,3})', viento_bloque or '')
    presion = get_first(r'Presi[oó]n\s+m[ií]nima\s+central\s*\[hpa\]\s*\|?\s*(\d{3,4})', text)

    lluvia = get_between(text, r'Pron[oó]stico\s+de\s+lluvia\s*\|?',
                         r'Zona\s+de\s+vigilancia|Comentarios\s+adicionales|Recomendaciones')
    vigilancia = get_between(text, r'Zona\s+de\s+vigilancia\s*\|?',
                              r'Comentarios\s+adicionales|Recomendaciones')
    comentarios = get_between(text, r'Comentarios\s+adicionales\s*\|?', r'Recomendaciones|Pron[oó]stico\s+de\s+evoluci[oó]n')
    movimiento = get_between(text, r'Desplazamiento\s+actual\s*\|?', r'Vientos\s+m[aá]ximos')
    referencia = get_between(text, r'Distancia\s+al\s+lugar\s+m[aá]s\s+cercano\s*\|?',
                              r'Desplazamiento\s+actual')

    # Para no mezclar los efectos, sólo se completan los apartados que
    # puedan identificarse en la fuente. Los demás siguen sin confirmar.
    viento_costero = get_first(r'((?:Se\s+pronostican|Se\s+prev[eé]n)\s+vientos?\s+[^.]+\.)', comentarios or '')
    oleaje = get_first(r'((?:Asimismo,\s*)?(?:se\s+)?(?:prev[eé]\s+)?oleaje\s+[^.]+\.)', comentarios or '')

    result = {
        'ciclon': 'Simón', 'fuente': 'SMN / CONAGUA',
        'url_oficial': PORTAL, 'url_datos': official_data_url,
        'aviso': int(number), 'fecha': fecha, 'hora': hora, 'gmt': gmt,
        'clasificacion': clasif,
        'lat': lat, 'lon': lon, 'referencia': referencia,
        'movimiento': movimiento,
        'viento': int(viento) if viento else None,
        'racha': int(racha) if racha else None,
        'presion': f'{presion} hPa' if presion else None,
        'vigilancia': vigilancia, 'lluvia': lluvia,
        'viento_costero': viento_costero, 'oleaje': oleaje,
        'mapa': map_url(soup, official_data_url),
        'pronostico': parse_forecast(soup),
        'estado_seguimiento': 'aviso_oficial',
    }
    if result['viento'] is not None and result['racha'] is not None and result['racha'] < result['viento']:
        raise ValueError('Inconsistencia: la racha es menor al viento sostenido.')
    return result


def previous():
    if not SALIDA.exists():
        return {}
    obj = json.loads(SALIDA.read_text(encoding='utf-8'))
    if obj.get('ciclon') != 'Simón':
        raise ValueError('datos_simon.json no corresponde a Simón; no se sobrescribirá.')
    return obj


def save(obj):
    tmp = SALIDA.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(SALIDA)


def preserve_last(obj):
    if obj.get('aviso') is not None and obj.get('fecha'):
        if obj.get('estado_seguimiento') != 'ultimo_aviso_conservado':
            preserved = dict(obj, estado_seguimiento='ultimo_aviso_conservado')
            save(preserved)
        print(f"Simón no figura como activo. Se conserva el último aviso oficial {obj['aviso']} ({obj['fecha']}).")
    else:
        print('Simón no figura como activo y no hay aviso histórico verificado. Datos no confirmados; sin cambios.')


async def update():
    previous_data = previous()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1600, 'height': 1000})
            await page.goto(PORTAL, wait_until='domcontentloaded', timeout=90000)
            await page.wait_for_timeout(9000)

            matches = []
            for frame in page.frames:
                for selector in ('a.aviso-btn[data-aviso-id]', '[data-aviso-id]'):
                    buttons = frame.locator(selector)
                    count = await buttons.count()
                    for idx in range(min(count, 30)):
                        button = buttons.nth(idx)
                        try:
                            raw_label = compact(await button.inner_text(timeout=2000))
                            if not STORM.search(raw_label) or not await button.is_visible():
                                continue
                            identifier = await button.get_attribute('data-aviso-id')
                            num_match = re.search(r'\bSim[oó]n\s+(\d+)\b', raw_label, re.I)
                            if identifier and identifier.isdigit() and num_match:
                                matches.append((frame, button, int(num_match.group(1)), identifier))
                        except Exception:
                            continue
                    if matches:
                        break
                if matches:
                    break

            if not matches:
                # No se toma automáticamente el aviso de Trudy u otro ciclón.
                # Un fallo de red, sin embargo, debe diferenciarse de la ausencia real.
                saw_buttons = False
                for f in page.frames:
                    if await f.locator('[data-aviso-id]').count():
                        saw_buttons = True
                        break
                if not saw_buttons:
                    raise RuntimeError('El SMN no devolvió botones de avisos; no se puede afirmar que Simón haya finalizado.')
                preserve_last(previous_data)
                return

            frame, button, selected_no, official_id = matches[0]
            print(f'Selector SMN: Simón {selected_no}; data-aviso-id={official_id}')
            await button.click(timeout=12000)
            target = None
            for _ in range(30):
                await page.wait_for_timeout(1000)
                for f in page.frames:
                    parsed = urlparse(f.url)
                    if 'WebAviso' in parsed.path and official_id in parse_qs(parsed.query).get('searchText', []):
                        target = f
                        break
                if target:
                    break
            if not target:
                raise RuntimeError('No se comprobó que el iframe abriera el aviso específico de Simón.')
            await target.locator('body').wait_for(timeout=25000)
            for _ in range(20):
                body = await target.locator('body').inner_text(timeout=5000)
                if re.search(r'Oc[eé]ano\s+Pac[ií]fico\s*-\s*No\.\s*Aviso\s*:', body, re.I):
                    break
                await page.wait_for_timeout(700)
            result = parse_notice(await target.content(), target.url, selected_no)
        finally:
            await browser.close()

    old_no = previous_data.get('aviso')
    if old_no is not None and result['aviso'] < int(old_no):
        raise ValueError(f"Retroceso bloqueado: Simón {result['aviso']} frente al aviso guardado {old_no}.")
    if previous_data.get('fecha') and result['fecha'] < previous_data['fecha']:
        raise ValueError('Retroceso bloqueado por fecha de emisión.')

    save(result)
    print(f"ACTUALIZADO: Simón, aviso {result['aviso']}, {result['clasificacion']}, {result['fecha']} {result['hora']}")
    print('Se conservaron los archivos históricos de Polo.')


if __name__ == '__main__':
    try:
        print('SEGUIMIENTO SIMÓN V1 | SMN/CONAGUA | PORTAL:', PORTAL, flush=True)
        asyncio.run(update())
    except Exception as exc:
        print('ERROR:', exc, file=sys.stderr)
        print('No se modificaron los datos con información no verificada.', file=sys.stderr)
        sys.exit(1)
