"""Custos do mandato, emendas ao Orçamento e gasto da Câmara, todos lidos em páginas oficiais da Câmara.

- Custos: página do deputado (camara.leg.br/deputados/ID?ano=AAAA), bloco "Gastos e recursos":
  cota parlamentar e verba de gabinete gastas no ano, mês a mês.
- Salário: Decreto Legislativo 172/2022 (valor do subsídio em cada mês), contado nos meses em que
  o deputado teve gasto de cota ou de gabinete registrado naquele ano.
- Emendas: camara.leg.br/deputados/ID/todas-emendas?ano=AAAA (valores autorizado, empenhado e pago).
- Gasto da Câmara: camara.leg.br/transparencia ("Despesas pagas em AAAA").
"""
import html as H
import math
import re
import unicodedata

MESES = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]


def num(s):
    return float(s.replace(".", "").replace(",", "."))


def subsidio(ano, mes):
    """Subsídio mensal bruto de deputado federal (Decreto Legislativo 172/2022). mes = 1..12"""
    if (ano, mes) < (2023, 4):
        return 39293.32
    if (ano, mes) < (2024, 2):
        return 41650.92
    if (ano, mes) < (2025, 2):
        return 44008.52
    return 46366.19


RE_COTA = re.compile(r"Cota Partamentar Total \(R\$\) Percentual Gasto ([\d.,]+) .*?Gasto mensal Mês Valor \(R\$\) Percentual (.*?) Detalhamento")
RE_GAB = re.compile(r"Verba de gabinete Total \(R\$\) Percentual Gasto ([\d.,]+) .*?Gasto mensal Mês Total \(R\$\) Percentual (.*?) Veja mais")
RE_MES = re.compile(r"\b(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ) (-?[\d.,]+)")
RE_GASTOS = re.compile(r"Gastos e recursos \d{4}")


def ler_custos(texto, ano):
    """texto = página do deputado já sem HTML. Devolve [cota, gabinete, salario, meses] do ano.
    Se a página não tem o bloco de gastos (deputado fora do mandato no ano), devolve zeros."""
    c = RE_COTA.search(texto)
    g = RE_GAB.search(texto)
    if not c and not g:
        return [0.0, 0.0, 0.0, 0]
    meses = set()
    cota = gab = 0.0
    if c:
        cota = num(c.group(1))
        meses |= {MESES.index(m) + 1 for m, v in RE_MES.findall(c.group(2)) if num(v) > 0}
    if g:
        gab = num(g.group(1))
        meses |= {MESES.index(m) + 1 for m, v in RE_MES.findall(g.group(2)) if num(v) > 0}
    sal = sum(subsidio(ano, m) for m in meses)
    mc, mg = [0.0] * 12, [0.0] * 12
    for m, v in (RE_MES.findall(c.group(2)) if c else []):
        mc[MESES.index(m)] = round(num(v), 2)
    for m, v in (RE_MES.findall(g.group(2)) if g else []):
        mg[MESES.index(m)] = round(num(v), 2)
    # 5º item: gasto mês a mês [cota x12, gabinete x12] (usado na ficha do deputado)
    return [round(cota, 2), round(gab, 2), round(sal, 2), len(meses), [mc, mg]]


# ------------------------------------------------------------------ emendas
RE_IT = re.compile(r'<li class="emendas__item">\s*<p class="emendas__destino">(.*?)</p>\s*<p class="emendas__descricao">(.*?)</p>(.*?)</ul>', re.S)
RE_V = re.compile(r'emendas-valores__titulo">\s*(Autorizado|Empenhado|Pago).*?emendas-valores__valor">\s*R\$\s*([\d.,]+)', re.S)
RE_TOT = re.compile(r"Exibindo resultados de <strong>(\d+)</strong> a <strong>(\d+)</strong> de <strong>(\d+)</strong>")
POR_PAGINA = 10


def ler_emendas(h):
    """Devolve (total_anunciado, itens) ou None se a página não for a lista de emendas."""
    if "Nenhum resultado encontrado" in h:
        return 0, []
    t = RE_TOT.search(h)
    if not t:
        return None
    itens = []
    for orgao, desc, rest in RE_IT.findall(h):
        v = {k: num(x) for k, x in RE_V.findall(rest)}
        itens.append([H.unescape(orgao).strip(), H.unescape(desc).strip(), v.get("Autorizado", 0.0), v.get("Empenhado", 0.0), v.get("Pago", 0.0)])
    return int(t.group(3)), itens


def paginas_necessarias(total):
    return max(1, math.ceil(total / POR_PAGINA))


def _n(s):
    s = "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


# Setor de cada emenda: regra única, pela ordem abaixo, olhando o órgão e a ação registrados pela Câmara.
SETORES = [
    ("Transferência especial (o município ou estado decide onde usar)", r"transferencias? especia"),
    ("Saúde", r"saude|hospital|sus\b|sanitari|vigilancia|ebserh|fiocruz|funasa"),
    ("Educação", r"educa|ensino|universidade|instituto federal|escola|fnde|capes|colegio|creche"),
    ("Segurança pública e defesa", r"seguranca|policia|penitenc|justica|defesa civil|comando d[oa]|exercito|marinha|aeronautica|defesa|bombeir|drogas|crime"),
    ("Assistência social e direitos", r"assistencia social|direitos humanos|mulher|crianca|adolescente|idos|deficiencia|desenvolvimento social|familia|combate a fome|igualdade racial|povos indigenas"),
    ("Agricultura", r"agricultura|agrari|pecuaria|agropecuaria|abastecimento|pesca|embrapa|incra|conab|rural"),
    ("Cidades, obras e desenvolvimento regional", r"cidades|urban|habitac|saneamento|integracao|desenvolvimento regional|codevasf|dnocs|sudene|sudam|sudeco|infraestrutura|pavimenta|obras|recursos hidricos"),
    ("Transportes", r"transporte|rodovi|dnit|porto|aeroport|ferrovi"),
    ("Esporte", r"esporte"),
    ("Cultura", r"cultura|patrimonio historico|iphan|ancine|biblioteca"),
    ("Turismo", r"turismo|embratur"),
    ("Ciência, tecnologia e comunicações", r"ciencia|tecnologia|inovacao|comunicac|cnpq|finep|telecomunic"),
    ("Meio ambiente", r"meio ambiente|ambiental|ibama|icmbio|florest|clima"),
    ("Trabalho e economia", r"trabalho|emprego|economia|industria|comercio|empreend|fazenda|desenvolvimento, industria"),
]
SETORES_RX = [(n, re.compile(r)) for n, r in SETORES]
OUTROS = "Outros"


def setor(orgao, desc):
    t = _n(orgao + " | " + desc)
    for n, rx in SETORES_RX:
        if rx.search(t):
            return n
    return OUTROS


NOMES_SETORES = [n for n, _ in SETORES] + [OUTROS]

# ------------------------------------------------------------------ gasto da Câmara
RE_CAMARA = re.compile(r"Despesas pagas em (\d{4}) valor gasto R\$ ([\d.,]+) (bilh(?:ão|ões)|milh(?:ão|ões)) o que corresponde a ([\d.,]+)% do orçamento previsto")


def ler_gasto_camara(texto):
    m = RE_CAMARA.search(texto)
    if not m:
        return None
    mult = 1e9 if m.group(3).startswith("bilh") else 1e6
    pago = num(m.group(2)) * mult
    pct = num(m.group(4))
    return {"ano": int(m.group(1)), "pago": pago, "pct": pct, "orcamento": pago / (pct / 100) if pct else None}
