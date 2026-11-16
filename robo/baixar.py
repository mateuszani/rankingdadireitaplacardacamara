"""Download dos dados oficiais da Câmara, com conferência de tamanho em bytes.

Os arquivos grandes da Câmara às vezes chegam cortados. Por isso cada arquivo é
baixado em pedaços de 1 MB (cabeçalho Range), e cada pedaço é conferido.
Se o total não bater com o tamanho que o servidor informa, o download falha.
"""
import os, time, json, concurrent.futures as cf
import requests

BASE = "https://dadosabertos.camara.leg.br"
UA = {"User-Agent": "placar-da-camara/1.0 (+https://github.com/mateuszani/rankingdadireitaplacardacamara)"}
S = requests.Session()
S.headers.update(UA)


class ColetaIncompleta(Exception):
    pass


def tamanho(url):
    for k in range(6):
        try:
            r = S.head(url, timeout=60, allow_redirects=True)
            if r.status_code == 200 and r.headers.get("content-length"):
                return int(r.headers["content-length"])
        except requests.RequestException:
            pass
        time.sleep(3 * (k + 1))
    raise ColetaIncompleta(f"sem tamanho para {url}")


def _pedaco(url, ini, fim):
    for k in range(10):
        try:
            r = S.get(url, headers={"Range": f"bytes={ini}-{fim}"}, timeout=120)
            if r.status_code == 206 and len(r.content) == fim - ini + 1:
                return r.content
        except requests.RequestException:
            pass
        time.sleep(2 * (k + 1))
    raise ColetaIncompleta(f"pedaço {ini}-{fim} de {url} não chegou inteiro")


def arquivo(url, destino, ch=1024 * 1024):
    """Baixa url em destino e devolve o número de bytes conferido."""
    n = tamanho(url)
    faixas = [(i, min(n, i + ch) - 1) for i in range(0, n, ch)]
    with cf.ThreadPoolExecutor(4) as ex:
        partes = list(ex.map(lambda f: _pedaco(url, *f), faixas))
    dados = b"".join(partes)
    if len(dados) != n:
        raise ColetaIncompleta(f"{url}: {len(dados)} bytes, esperado {n}")
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, "wb") as f:
        f.write(dados)
    return n


def api(caminho, tentativas=6):
    url = caminho if caminho.startswith("http") else f"{BASE}/api/v2/{caminho}"
    for k in range(tentativas):
        try:
            r = S.get(url, headers={"Accept": "application/json"}, timeout=60)
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(3 * (k + 1))
    raise ColetaIncompleta(f"API não respondeu: {url}")


def pagina(url, tentativas=5, minimo=20000):
    for k in range(tentativas):
        try:
            r = S.get(url, timeout=60)
            if r.status_code == 200 and len(r.text) > minimo:
                return r.text
        except requests.RequestException:
            pass
        time.sleep(2 * (k + 1))
    return None
