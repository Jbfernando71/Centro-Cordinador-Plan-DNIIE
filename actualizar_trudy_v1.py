#!/usr/bin/env python3
"""Lee el aviso específico de Trudy en SMN; conserva el último dato ante fallos."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from bs4 import BeautifulSoup

PORTAL='https://smn.conagua.gob.mx/es/pronosticos/avisos/aviso-de-ciclon-tropical-en-el-oceano-pacifico'
WEB='https://smn.conagua.gob.mx/tools/GUI/PortalLaravel/public/WebAviso'
OUT=Path(__file__).resolve().parent/'datos_trudy.json'
compact=lambda s:re.sub(r'\s+',' ',s).strip()

def fetch(url):
    if urlparse(url).hostname!='smn.conagua.gob.mx':raise ValueError('Origen no permitido')
    with urlopen(Request(url,headers={'User-Agent':'Centro-Coordinador-DN-III-E/1.0'}),timeout=60) as r:
        if urlparse(r.url).hostname!='smn.conagua.gob.mx':raise ValueError('Redirección fuera del SMN')
        return r.read().decode('utf-8')

def parse_notice(html,url,number):
    soup=BeautifulSoup(html,'html.parser')
    for n in soup(['script','style']):n.decompose()
    text=compact(soup.get_text(' ',strip=True))
    synthesis=soup.select_one('.sintesis-box')
    summary=compact(synthesis.get_text(' ',strip=True)) if synthesis else ''
    if not re.search(r'\bTrudy\b',summary,re.I):raise ValueError('La síntesis no corresponde a Trudy')
    m=re.search(r'No\.\s*Aviso:\s*(\d+)',text,re.I)
    if not m or int(m[1])!=number:raise ValueError('Número de aviso no coincide con selector')
    emission=re.search(r'Emisi[oó]n:\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})',text)
    if not emission:raise ValueError('Fecha de emisión ausente')
    fields={}
    for tr in soup.select('table tr'):
        cells=[compact(td.get_text(' ',strip=True)) for td in tr.find_all('td',recursive=False)]
        if len(cells)>=2:fields[cells[0]]=' '.join(cells[1:])
    def field(prefix):return next((v for k,v in fields.items() if k.lower().startswith(prefix.lower())),None)
    def extract(pattern,value):
        m=re.search(pattern,value or '',re.I)
        if not m:raise ValueError('Campo obligatorio no confirmado: '+pattern)
        return m[1]
    coordinates=field('Ubicación del centro')
    lat=extract(r'Latitud Norte:\s*([\d.]+)',coordinates)
    lon=extract(r'Longitud Oeste:\s*([\d.]+)',coordinates)
    winds=field('Vientos máximos')
    sustained=int(extract(r'Sostenidos:\s*(\d+)',winds));gust=int(extract(r'Rachas:\s*(\d+)',winds))
    if not 0<=float(lat)<=90 or not 0<=float(lon)<=180 or gust<sustained:raise ValueError('Condiciones inconsistentes')
    category=re.search(r'hurac[aá]n.*?categor[ií]a\s*([1-5])',summary,re.I)
    if category:classification='Huracán categoría '+category[1]
    elif re.search('baja presión remanente',summary,re.I):classification='Baja presión remanente'
    elif re.search('post.tropical|remanentes',summary,re.I):classification='Ciclón post-tropical'
    elif re.search('tormenta tropical',summary,re.I):classification='Tormenta tropical'
    elif re.search('depresión tropical',summary,re.I):classification='Depresión tropical'
    else:raise ValueError('Clasificación sin confirmar')
    comments=field('Comentarios adicionales') or ''
    split=re.search(r'(?:Así como|Asimismo[,]?)\s+(?=oleaje)',comments,re.I)
    wind=comments[:split.start()].strip() if split else None
    waves=comments[split.end():].strip() if split else None
    forecast=[]
    for table in soup.find_all('table'):
        if not re.search(r'D[ií]a/Hora',table.get_text(' ',strip=True)):continue
        for tr in table.find_all('tr'):
            cells=[compact(c.get_text(' ',strip=True)) for c in tr.find_all('td')]
            if len(cells)==6 and re.fullmatch(r'\d{1,2}/\d{2}h',cells[0]):
                forecast.append(dict(zip(['dia_hora','lat','lon','viento','categoria','ubicacion'],cells)))
    def image(kind):
        for img in soup.find_all('img',src=True):
            src=urljoin(url,img['src'])
            if kind in src and urlparse(src).hostname=='smn.conagua.gob.mx':return src
        return None
    local=field('Hora local')
    gmt=re.search(r'\((\d{2}:\d{2})\s+horas GMT',local or '')
    return dict(ciclon='Trudy',fuente='SMN / CONAGUA',url_oficial=PORTAL,url_datos=url,
        aviso=number,fecha=emission[1],hora=emission[2],gmt=gmt[1] if gmt else None,
        hora_oficial_texto=local,clasificacion=classification,lat=lat,lon=lon,
        referencia=field('Distancia al lugar'),movimiento=field('Desplazamiento'),viento=sustained,racha=gust,
        presion=(field('Presión mínima')+' hPa') if field('Presión mínima') else None,
        vigilancia=field('Zona de vigilancia'),lluvia=field('Pronóstico de lluvia'),
        viento_costero=wind,oleaje=waves,mapa=image('ImgTray'),satelite=image('ImgSat'),
        pronostico=forecast,estado_seguimiento='aviso_oficial',verificado_utc=datetime.now(timezone.utc).isoformat())

def save(data):
    temp=OUT.with_suffix('.json.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');temp.replace(OUT)

def update():
    old=json.loads(OUT.read_text()) if OUT.exists() else {}
    soup=BeautifulSoup(fetch(WEB),'html.parser')
    buttons=soup.select('a.aviso-btn[data-aviso-id]')
    if not buttons:raise ValueError('SMN sin selector de avisos; se conservan datos previos')
    matches=[]
    for b in buttons:
        m=re.search(r'\bTrudy\s+(\d+)\b',compact(b.get_text(' ',strip=True)),re.I)
        if m and str(b['data-aviso-id']).isdigit():matches.append((int(m[1]),b['data-aviso-id']))
    if not matches:
        if old.get('ciclon')=='Trudy':save(dict(old,estado_seguimiento='ultimo_aviso_conservado'))
        print('Trudy no figura entre avisos activos; último aviso conservado.');return
    number,identifier=max(matches)
    url=WEB+'?searchText='+identifier
    result=parse_notice(fetch(url),url,number)
    if old.get('ciclon')=='Trudy' and (result['aviso']<old.get('aviso',0) or (result['fecha'],result['hora'])<(old.get('fecha',''),old.get('hora',''))):raise ValueError('Retroceso de aviso bloqueado')
    save(result);print('Trudy actualizado:',number,result['fecha'],result['hora'])

if __name__=='__main__':update()
