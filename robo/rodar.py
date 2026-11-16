"""Robô do Placar da Câmara.

1. Baixa de novo os dados oficiais da Câmara (arquivos de votações, proposições,
   lista de deputados e a página oficial de cada deputado).
2. Calcula tudo o que o site mostra.
3. Confere se a coleta veio completa e normal (ver conferir()).
4. Só se passar na conferência, monta o site em ./saida para ser publicado.

Se qualquer conferência falhar, o programa termina com erro e nada é publicado:
o site no ar continua sendo a última versão boa.

Uso:  python robo/rodar.py            (coleta tudo na internet)
      python robo/rodar.py --local DIR --off OFF.json   (teste com arquivos já baixados)
"""
import argparse, collections, datetime as dt, json, os, re, sys, time, unicodedata
import concurrent.futures as cf
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
import baixar
import custos
from classificar import grupo, etapa

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANOS = [2023, 2024, 2025, 2026]  # 57ª legislatura (o site foi desenhado para estes 4 anos)
SITE_URL = "https://mateuszani.github.io/rankingdadireitaplacardacamara/"
CENTRO = ["MDB", "PSD", "PP", "UNIÃO", "REPUBLICANOS"]
PARTIDOS = ['PL', 'PT', 'UNIÃO', 'PP', 'PSD', 'REPUBLICANOS', 'MDB', 'PSB', 'PDT', 'PSOL', 'PODE', 'PSDB', 'NOVO', 'MISSÃO',
            'PCdoB', 'AVANTE', 'SOLIDARIEDADE', 'PV', 'CIDADANIA', 'REDE', 'PRD']
LOG = []


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)


def norm(s):
    s = str(s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- coleta
ARQS = ["votacoes", "votacoesVotos", "votacoesOrientacoes", "proposicoes", "proposicoesAutores"]


def coletar_arquivos(dirdados, local=None):
    meta = {}
    for ano in ANOS:
        for k in ARQS:
            nome = f"{k}-{ano}.csv"
            dest = os.path.join(dirdados, nome)
            if local:
                src = os.path.join(local, "camara-" + nome)
                os.makedirs(dirdados, exist_ok=True)
                if not os.path.exists(dest):
                    os.symlink(os.path.abspath(src), dest)
                meta[nome] = os.path.getsize(src)
            else:
                url = f"{baixar.BASE}/arquivos/{k}/csv/{nome}"
                meta[nome] = baixar.arquivo(url, dest)
            log(f"  {nome}: {meta[nome]:,} bytes conferidos")
    return meta


def ler(dirdados, k, cols):
    out = []
    for ano in ANOS:
        df = pd.read_csv(os.path.join(dirdados, f"{k}-{ano}.csv"), sep=";", dtype=str, usecols=cols, encoding="utf-8-sig")
        out.append(df)
    return pd.concat(out, ignore_index=True)


RE_PROP = re.compile(r"Propostas legislativas\s*de sua autoria\s*(\d+)")
RE_VOT = re.compile(r"Votações nominais\s*\??\s*em Plenário\s*(\d+)")
RE_PRES = re.compile(r"Presença em Plenário.*?Presenças(?: na Câmara)?\s*(\d+)\s*dias?\s*Ausências justificadas\s*(\d+)\s*dias?\s*Ausências não justificadas\s*(\d+)\s*dias?")


def texto_html(h):
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = h.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", h)


def ler_pagina(html):
    x = texto_html(html)
    a, b, c = RE_PROP.search(x), RE_VOT.search(x), RE_PRES.search(x)
    if not (a and b and c):
        return None
    return [int(a.group(1)), int(b.group(1)), int(c.group(1)), int(c.group(2)), int(c.group(3))]


def coletar_paginas(ids):
    """Página oficial de cada deputado em cada ano: presença, propostas e custos do mandato."""
    tarefas = [(i, a) for i in ids for a in ANOS]
    res, cus = {}, {}

    def um(t):
        i, a = t
        h = baixar.pagina(f"https://www.camara.leg.br/deputados/{i}?ano={a}")
        if not h:
            return t, None, None
        r = ler_pagina(h)
        return t, r, (custos.ler_custos(texto_html(h), a) if r else None)

    with cf.ThreadPoolExecutor(6) as ex:
        for t, r, c in ex.map(um, tarefas):
            res[t], cus[t] = r, c
    falhas = [t for t, r in res.items() if r is None]
    for t in falhas:  # segunda chance, devagar
        time.sleep(1)
        _, res[t], cus[t] = um(t)
    return res, cus


def coletar_emendas(ids):
    """Lista oficial de emendas ao Orçamento de cada deputado, ano a ano, com todas as páginas."""
    res = {}

    def pag(i, a, n):
        h = baixar.pagina(f"https://www.camara.leg.br/deputados/{i}/todas-emendas?ano={a}&pagina={n}", minimo=15000)
        return custos.ler_emendas(h) if h else None

    def um(t):
        i, a = t
        for _ in range(3):
            r = pag(i, a, 1)
            if r is not None:
                break
            time.sleep(2)
        if r is None:
            return t, None
        total, itens = r
        for n in range(2, custos.paginas_necessarias(total) + 1):
            rn = None
            for _ in range(3):
                rn = pag(i, a, n)
                if rn is not None:
                    break
                time.sleep(2)
            if rn is None:
                return t, None
            itens += rn[1]
        return t, (total, itens)

    with cf.ThreadPoolExecutor(6) as ex:
        for t, r in ex.map(um, [(i, a) for i in ids for a in ANOS]):
            res[t] = r
    return res


def coletar_gasto_camara():
    h = baixar.pagina("https://www.camara.leg.br/transparencia/", minimo=15000)
    return custos.ler_gasto_camara(texto_html(h)) if h else None


def calcular_custos_emendas(deps, CUS, EM, OFF, DEPS, RES, saida):
    """Custo do mandato, emendas pagas por setor e a nota de cada deputado."""
    SET = custos.NOMES_SETORES
    si = {s: k for k, s in enumerate(SET)}
    CU, EMD, shards = [], [], collections.defaultdict(dict)
    setor_tot = [0.0] * len(SET)
    for d in deps:
        i = d["id"]
        anos = [CUS.get((i, a)) or [0, 0, 0, 0] for a in ANOS]
        tot = round(sum(x[0] + x[1] + x[2] for x in anos), 2)
        CU.append([i, tot] + sum([list(x) for x in anos], []))
        aut = emp = pago = 0.0; por = [0.0] * len(SET); itens = []
        for a in ANOS:
            r = EM.get((i, a))
            for org, desc, au, em, pg in (r[1] if r else []):
                s = si[custos.setor(org, desc)]
                aut += au; emp += em; pago += pg; por[s] += pg; setor_tot[s] += pg
                itens.append([a, org, desc, au, em, pg, s])
        EMD.append([i, round(aut, 2), round(emp, 2), round(pago, 2), len(itens), [round(x, 2) for x in por]])
        itens.sort(key=lambda x: (-x[0], -x[5]))
        shards[i % 64][i] = itens
    os.makedirs(os.path.join(saida, "e"), exist_ok=True)
    for k in range(64):
        json.dump(shards.get(k, {}), open(os.path.join(saida, "e", f"{k}.json"), "w"), ensure_ascii=False, separators=(",", ":"))

    # ---- nota: 4 critérios, mesma regra para todos (ver texto "Como a nota é calculada" no site)
    dep_by = {r[0]: r for r in DEPS}
    res_by = {r[0]: r for r in RES["idx"]}
    cu_by = {r[0]: r for r in CU}
    vals = {"pres": {}, "vot": {}, "leis": {}, "custo": {}}
    for d in deps:
        i = d["id"]
        p = [OFF.get((i, a)) for a in ANOS]
        pres = sum(x[2] for x in p if x); dias = sum(x[2] + x[3] + x[4] for x in p if x)
        if dias >= 20:
            vals["pres"][i] = 100 * pres / dias
        r = dep_by.get(i)
        if r and r[5] >= 50:
            vals["vot"][i] = 100 * r[4] / r[5]
        vals["leis"][i] = res_by.get(i, [0] * 9)[8]
        c = cu_by[i]; meses = sum(c[2 + 4 * k + 3] for k in range(len(ANOS)))
        gasto = sum(c[2 + 4 * k] + c[2 + 4 * k + 1] for k in range(len(ANOS)))
        if meses >= 3:
            vals["custo"][i] = gasto / meses

    def escala(v, todos, maior_melhor=True):
        menos = sum(1 for x in todos if x < v); iguais = sum(1 for x in todos if x == v)
        q = (menos + 0.5 * (iguais - 1)) / max(1, len(todos) - 1)
        return round(10 * (q if maior_melhor else 1 - q), 1)

    todos = {k: sorted(v.values()) for k, v in vals.items()}
    NOTA = []
    for d in deps:
        i = d["id"]; sc = []; raw = []
        for k, mm in [("pres", True), ("vot", True), ("leis", True), ("custo", False)]:
            v = vals[k].get(i)
            raw.append(None if v is None else round(v, 2))
            sc.append(None if v is None else escala(v, todos[k], mm))
        ok = [x for x in sc if x is not None]
        NOTA.append([i, round(sum(ok) / len(ok), 1) if ok else None] + sc + raw)
    stats = {"custo_total": round(sum(r[1] for r in CU), 2), "emendas_pago": round(sum(r[3] for r in EMD), 2),
             "dep_com_custo": sum(1 for r in CU if r[1] > 0), "setores": [round(x, 2) for x in setor_tot]}
    return CU, EMD, NOTA, SET, stats


# ---------------------------------------------------------------- cálculos de votações
def pos_of(s, n, o):
    if s > n + o:
        return "S"
    if n + o > s:
        return "N"
    return None


def calcular_votacoes(dd, deps, titulos):
    vot = ler(dd, "votacoes", ["id", "data", "siglaOrgao", "descricao", "votosSim", "votosNao"])
    vv = ler(dd, "votacoesVotos", ["idVotacao", "voto", "deputado_id", "deputado_siglaPartido"])
    ori = ler(dd, "votacoesOrientacoes", ["idVotacao", "siglaOrgao", "siglaBancada", "orientacao"])
    plen = vot[vot.siglaOrgao == "PLEN"].drop_duplicates("id").set_index("id")
    vv = vv[vv.idVotacao.isin(plen.index)].copy()
    vv["data"] = vv.idVotacao.map(plen.data)
    vv["ano"] = vv.data.str[:4]
    vv["k"] = vv.voto.map({"Sim": "s", "Não": "n", "Obstrução": "o"}).fillna("a")
    ids = sorted(vv.idVotacao.unique(), key=lambda i: plen.data[i])
    # contagem por votação e partido
    cnt = vv.groupby(["idVotacao", "deputado_siglaPartido", "k"]).size().unstack(fill_value=0)
    for c in "snoa":
        if c not in cnt.columns:
            cnt[c] = 0
    POS = collections.defaultdict(dict)
    for (i, p), r in cnt.iterrows():
        POS[i][p] = (int(r.s), int(r.n), int(r.o))
    data = {i: plen.data[i] for i in ids}

    def dividida(i):
        s = sum(v[0] for v in POS[i].values()); n = sum(v[1] + v[2] for v in POS[i].values())
        t = s + n
        return t > 0 and min(s, n) / t >= 0.2

    DIV = {i: dividida(i) for i in ids}

    def calc(a, bs, filtro=None):
        tog = tot = 0
        for i in ids:
            if filtro and not filtro(i):
                continue
            m = POS[i]
            if a not in m:
                continue
            pa = pos_of(*m[a])
            agg = [0, 0, 0]
            for b in bs:
                if b in m:
                    agg = [x + y for x, y in zip(agg, m[b])]
            pb = pos_of(*agg)
            if not pa or not pb:
                continue
            tot += 1
            tog += pa == pb
        return [tog, tot]

    missao_votos = sorted(data[i] for i in ids if "MISSÃO" in POS[i])
    missao_desde = missao_votos[0] if missao_votos else None
    R = {}
    for A in ["PL", "NOVO", "MISSÃO"]:
        R[A] = {}
        for lab, B in [("PT", ["PT"]), ("CENTRO", CENTRO)]:
            d = {"todas": calc(A, B), "divididas": calc(A, B, lambda i: DIV[i])}
            for y in ANOS:
                r = calc(A, B, lambda i, y=y: data[i].startswith(str(y)))
                if r[1] or A != "MISSÃO":
                    d[str(y)] = r
            R[A][lab] = d
    jan = (lambda i: data[i] >= missao_desde) if missao_desde else (lambda i: False)
    R["janela"] = {"PL_PT": calc("PL", ["PT"], jan), "NOVO_PT": calc("NOVO", ["PT"], jan), "MIS_PT": calc("MISSÃO", ["PT"], jan),
                   "PL_C": calc("PL", CENTRO, jan), "NOVO_C": calc("NOVO", CENTRO, jan), "MIS_C": calc("MISSÃO", CENTRO, jan)}
    R["porCentro"] = {A: {c: calc(A, [c]) for c in CENTRO} for A in ["PL", "NOVO", "MISSÃO"]}
    R["porCentroDiv"] = {A: {c: calc(A, [c], lambda i: DIV[i]) for c in CENTRO} for A in ["PL", "NOVO", "MISSÃO"]}
    porano = collections.Counter(data[i][:4] for i in ids)
    R["meta"] = {"votacoes": len(ids), "porAno": {str(y): porano.get(str(y), 0) for y in ANOS}, "divididas": sum(DIV.values()),
                 "ultimaVotacao": max(data.values()), "missaoDesde": missao_desde}
    # matriz de partidos e governo
    P = [p for p in PARTIDOS if any(p in POS[i] for i in ids)]
    M = [[None if a == b else calc(a, [b]) for b in P] for a in P]
    og = ori[(ori.siglaOrgao == "PLEN") & (ori.siglaBancada == "Governo") & ori.orientacao.isin(["Sim", "Não"])]
    GOV = {r.idVotacao: ("S" if r.orientacao == "Sim" else "N") for r in og.itertuples()}
    G = {}
    for a in P:
        t = g = 0
        for i in ids:
            if i in GOV and a in POS[i]:
                p = pos_of(*POS[i][a])
                if p:
                    t += 1; g += p == GOV[i]
        G[a] = [g, t]
    MX = {"P": P, "M": M, "G": G}
    # estatísticas por deputado
    dep_ids = [str(d["id"]) for d in deps]
    mine = vv[vv.deputado_id.isin(dep_ids)]
    pos_dt = sorted(data.values())
    st = {}
    for r in mine.itertuples():
        s = st.setdefault(r.deputado_id, {"n": 0, "S": 0, "N": 0, "O": 0, "A": 0, "first": r.data, "last": r.data,
                                           "part": 0, "partT": 0, "pt": 0, "ptT": 0, "pl": 0, "plT": 0, "gov": 0, "govT": 0,
                                           "vy": collections.Counter()})
        s["n"] += 1
        k = {"s": "S", "n": "N", "o": "O"}.get(r.k, "A")
        s[k] += 1
        if r.voto in ("Sim", "Não", "Obstrução", "Abstenção"):
            s["vy"][r.ano] += 1
        s["first"] = min(s["first"], r.data); s["last"] = max(s["last"], r.data)
        mine_v = "S" if r.k == "s" else ("N" if r.k in "no" else None)
        if not mine_v:
            continue
        m = POS[r.idVotacao]
        c = m.get(r.deputado_siglaPartido)
        if c:
            o = (c[0] - (r.k == "s"), c[1] - (r.k == "n"), c[2] - (r.k == "o"))
            pp = pos_of(*o)
            if pp:
                s["partT"] += 1; s["part"] += pp == mine_v
        if r.deputado_siglaPartido != "PT" and "PT" in m:
            pp = pos_of(*m["PT"])
            if pp:
                s["ptT"] += 1; s["pt"] += pp == mine_v
        if r.deputado_siglaPartido != "PL" and "PL" in m:
            pp = pos_of(*m["PL"])
            if pp:
                s["plT"] += 1; s["pl"] += pp == mine_v
        if r.idVotacao in GOV:
            s["govT"] += 1; s["gov"] += GOV[r.idVotacao] == mine_v
    import bisect
    pc = lambda a, b: round(100 * a / b, 1) if b else ""
    DEPS, VYv = [], []
    for d in deps:
        s = st.get(str(d["id"]))
        if not s:
            DEPS.append([d["id"], d["nome"], d["siglaPartido"], d["siglaUf"], 0, 0, 0, 0, 0, 0, "", "", "", ""])
            VYv.append([d["id"], 0, 0, 0, 0])
            continue
        tot = bisect.bisect_right(pos_dt, s["last"]) - bisect.bisect_left(pos_dt, s["first"])
        DEPS.append([d["id"], d["nome"], d["siglaPartido"], d["siglaUf"], s["n"], tot, s["S"], s["N"], s["O"], s["A"],
                     pc(s["part"], s["partT"]), pc(s["pt"], s["ptT"]), pc(s["pl"], s["plT"]), pc(s["gov"], s["govT"])])
        VYv.append([d["id"]] + [s["vy"].get(str(y), 0) for y in ANOS])
    VY = {"tot": [porano.get(str(y), 0) for y in ANOS], "v": VYv}
    # votações principais que dividiram o plenário (2025 em diante)
    sel = []
    rx_ok = re.compile(r"(Projeto de Lei|Projeto de Decreto|Substitutivo|Proposta de Emenda|Medida Provis)", re.I)
    rx_no = re.compile(r"(Emendas? de Plenário|Emendas? do Senado|Destaque|destacad|Requerimento|Subemenda|Emenda Aglutinativa|preliminar|Parecer)", re.I)
    simnao = vv.groupby(["idVotacao", "voto"]).size().unstack(fill_value=0)
    titmap = {t[0]: t for t in titulos}
    for i in sorted(ids, key=lambda i: data[i], reverse=True):
        desc = str(plen.descricao[i])
        if data[i] < "2025-01-01" or not DIV[i] or not re.match(r"^(Aprovad|Rejeitad)", desc) or not rx_ok.search(desc) or rx_no.search(desc):
            continue
        sim = int(simnao.loc[i].get("Sim", 0)); nao = int(simnao.loc[i].get("Não", 0))
        if i in titmap:
            t = titmap[i]
            sel.append([i, data[i], t[2], t[3], sim, nao, t[6]])
        else:
            sig, pid, tit = proposicao_da_votacao(i, desc)
            sel.append([i, data[i], sig, pid, sim, nao, tit])
    # conferência do placar oficial de cada votação selecionada
    placar_ok = []
    for v in sel:
        oficial = (int(plen.votosSim[v[0]] or 0), int(plen.votosNao[v[0]] or 0))
        placar_ok.append((v[0], (v[4], v[5]) == oficial, (v[4], v[5]), oficial))
    cod = {}
    idx = {v[0]: k for k, v in enumerate(sel)}
    for r in mine[mine.idVotacao.isin(idx)].itertuples():
        a = cod.setdefault(r.deputado_id, ["-"] * len(sel))
        a[idx[r.idVotacao]] = {"Sim": "S", "Não": "N", "Obstrução": "O", "Abstenção": "A", "Artigo 17": "P"}.get(r.voto, "-")
    COD = [[d["id"], "".join(cod.get(str(d["id"]), ["-"] * len(sel)))] for d in deps]
    stats = {"plen_por_ano": R["meta"]["porAno"], "votacoes": len(ids), "votos": int(len(vv)), "placar": placar_ok}
    return R, MX, DEPS, VY, sel, COD, stats


def proposicao_da_votacao(i, desc):
    """Para uma votação nova, busca a proposição votada e usa a ementa oficial como título."""
    try:
        j = baixar.api(f"votacoes/{i}")["dados"]
        pa = (j.get("proposicoesAfetadas") or j.get("objetosPossiveis") or [None])[0]
        if pa:
            return f"{pa['siglaTipo']} {pa['numero']}/{pa['ano']}", pa["id"], cortar(pa.get("ementa") or desc, 220)
    except Exception:
        pass
    pid = int(i.split("-")[0])
    return "", pid, cortar(desc, 220)


def cortar(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 3].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------- propostas
UF = {'Acre': 'AC', 'Alagoas': 'AL', 'Amapá': 'AP', 'Amazonas': 'AM', 'Bahia': 'BA', 'Ceará': 'CE', 'Distrito Federal': 'DF',
      'Espírito Santo': 'ES', 'Goiás': 'GO', 'Maranhão': 'MA', 'Mato Grosso do Sul': 'MS', 'Mato Grosso': 'MT', 'Minas Gerais': 'MG',
      'Pará': 'PA', 'Paraíba': 'PB', 'Paraná': 'PR', 'Pernambuco': 'PE', 'Piauí': 'PI', 'Rio de Janeiro': 'RJ',
      'Rio Grande do Norte': 'RN', 'Rio Grande do Sul': 'RS', 'Rondônia': 'RO', 'Roraima': 'RR', 'Santa Catarina': 'SC',
      'São Paulo': 'SP', 'Sergipe': 'SE', 'Tocantins': 'TO'}
RX_UF = re.compile(r"(?<![\wÀ-ú])(" + "|".join(re.escape(n) for n in sorted(UF, key=len, reverse=True)) + r")(?![\wÀ-ú])")
RX_UF2 = re.compile(r"[/\-–(]\s?(" + "|".join(UF.values()) + r")\b")
GEN = re.compile(r"^(altera|acrescenta|modifica|revoga|da nova redacao)( a| o| os| as)? (redacao (d[oa] )?)?(lei|decreto|artigo|art|inciso|paragrafo|dispositivo|dispositivos|codigo|constituicao)\b")
VAGO = re.compile(r"que especifica|ora formulad|em anexo|conforme (o )?anexo|abaixo|a seguir")


def ufs(t):
    t = str(t or "")
    return sorted({UF[x] for x in RX_UF.findall(t)} | set(RX_UF2.findall(t)))


def chave(s):
    s = norm(s)
    s = re.split(r"nova ementa", s)[0]
    s = re.sub(r",? e da outras providencias.*$", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def calcular_propostas(dd, deps, saida):
    P = ler(dd, "proposicoes", ["id", "siglaTipo", "numero", "ano", "descricaoTipo", "ementa", "keywords", "dataApresentacao", "ultimoStatus_descricaoSituacao"]).drop_duplicates("id")
    A = ler(dd, "proposicoesAutores", ["idProposicao", "idDeputadoAutor", "nomeAutor", "siglaPartidoAutor", "siglaUFAutor", "ordemAssinatura"])
    A = A[A.idDeputadoAutor.notna() & (A.idDeputadoAutor != "")].drop_duplicates(["idProposicao", "idDeputadoAutor"])
    P = P[P.id.isin(set(A.idProposicao))].copy()
    gs = [grupo(r) for r in P.itertuples()]
    P["g"] = [g[0] for g in gs]; P["sub"] = [g[1] for g in gs]
    P["et"] = [etapa(s) if g == "L" else 0 for s, g in zip(P.ultimoStatus_descricaoSituacao, P.g)]
    P["dt"] = P.dataApresentacao.str[:10]
    P["lab"] = P.siglaTipo + " " + P.numero.astype(str) + "/" + P.ano.astype(str)
    P["em"] = P.ementa.map(lambda s: cortar(s, 170) if isinstance(s, str) else "")
    SUBS = sorted(P["sub"].dropna().unique().tolist())
    si = {s: i for i, s in enumerate(SUBS)}
    P["si"] = P["sub"].map(lambda s: si.get(s, -1) if isinstance(s, str) else -1)
    P = P.set_index("id", drop=False)
    ids = {str(d["id"]) for d in deps}
    Ad = A[A.idDeputadoAutor.isin(ids) & A.idProposicao.isin(P.index)]
    GO = {"L": 0, "M": 1, "P": 2, "H": 3, "O": 4}
    shards = collections.defaultdict(dict)
    idx = []
    pj = collections.Counter()
    for dep, grp in Ad.groupby("idDeputadoAutor"):
        pp = P.loc[grp.idProposicao]
        pr = dict(zip(grp.idProposicao, grp.ordemAssinatura == "1"))
        pp = pp.assign(go=pp.g.map(GO)).sort_values(["go", "et", "dt"], ascending=[True, True, False])
        items = [[int(r.id), r.lab, r.dt, r.g, int(r.si), int(r.et), int(bool(pr.get(r.id))), r.em] for r in pp.itertuples()]
        shards[int(dep) % 64][int(dep)] = items
        vc = pp.g.value_counts()
        aprov = int(sum(1 for r in pp.itertuples() if r.g == "L" and r.et in (1, 2) and pr.get(r.id)))
        idx.append([int(dep)] + [int(vc.get(k, 0)) for k in "LMPHO"] + [int((pp.et == 1).sum()), sum(pr.values()), aprov])
        pj[int(dep)] = int(pp.siglaTipo.isin(["PL", "PLP", "PEC", "PDL"]).sum())
    for d in deps:
        if str(d["id"]) not in set(Ad.idDeputadoAutor):
            idx.append([d["id"], 0, 0, 0, 0, 0, 0, 0, 0]); shards[d["id"] % 64][d["id"]] = []
    os.makedirs(os.path.join(saida, "s"), exist_ok=True)
    for k in range(64):
        json.dump(shards.get(k, {}), open(os.path.join(saida, "s", f"{k}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    RES = {"subs": SUBS, "idx": idx}
    PP = [[d["id"], 0, 0, pj.get(d["id"], 0), 0] for d in deps]
    # busca por estado/assunto e repetidas (grupos L, M, P, H)
    B = P[P.g.isin(list("LMPH"))].copy()
    B["ufs"] = (B.ementa.fillna("") + " | " + B.keywords.fillna("")).map(ufs)
    Ab = A[A.idProposicao.isin(B.index)].copy()
    Ab["o"] = pd.to_numeric(Ab.ordemAssinatura, errors="coerce").fillna(999)
    Ab = Ab.sort_values(["idProposicao", "o"])
    nomes = {}
    for r in Ab.drop_duplicates("idDeputadoAutor", keep="last").itertuples():
        nomes[int(r.idDeputadoAutor)] = [r.nomeAutor, r.siglaPartidoAutor if isinstance(r.siglaPartidoAutor, str) else "",
                                         r.siglaUFAutor if isinstance(r.siglaUFAutor, str) else ""]
    aut = Ab.groupby("idProposicao").idDeputadoAutor.apply(lambda s: [int(x) for x in s]).to_dict()
    os.makedirs(os.path.join(saida, "busca"), exist_ok=True)
    for y in ANOS:
        d = B[B.dt.str[:4] == str(y)].sort_values("dt", ascending=False)
        items = [[int(r.id), r.lab, r.dt, r.g, int(r.si), int(r.et), cortar(r.ementa, 220), cortar(r.keywords, 160) if r.g == "L" and isinstance(r.keywords, str) else "",
                  ",".join(r.ufs), aut.get(r.id, [])] for r in d.itertuples()]
        json.dump(items, open(os.path.join(saida, "busca", f"b{y}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump(nomes, open(os.path.join(saida, "busca", "nomes.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    B["k"] = B.ementa.map(chave)
    rep = []
    for (t, k), d in B.groupby(["siglaTipo", "k"]):
        if len(d) < 2 or len(k) < 25 or (GEN.match(k) and " para " not in k and " a fim de " not in k and " com o objetivo " not in k) or VAGO.search(k):
            continue
        d = d.sort_values("dt")
        rep.append([t, cortar(d.ementa.iloc[0], 260), len(d), ",".join(sorted(set(sum(list(d.ufs), [])))), d.g.iloc[0],
                    [[int(r.id), r.lab, r.dt, (aut.get(r.id) or [0])[0], len(aut.get(r.id, [])),
                      r.ultimoStatus_descricaoSituacao if isinstance(r.ultimoStatus_descricaoSituacao, str) else ""] for r in d.itertuples()]])
    rep.sort(key=lambda g: -g[2])
    json.dump(rep, open(os.path.join(saida, "busca", "repetidas.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    tot_dep = {int(dep): len(g) for dep, g in Ad.groupby("idDeputadoAutor")}
    stats = {"propostas": int(len(P)), "assinaturas": int(len(Ad)), "assinaturas_todas": int(len(A)),
             "grupos": {g: int(n) for g, n in P.g.value_counts().items()}, "repetidas": len(rep), "tot_dep": tot_dep}
    return RES, PP, stats


# ---------------------------------------------------------------- conferência
def conferir(atual, anterior, OFF, deps, stats_v, stats_p, CUS=None, EM=None, CAM=None, stats_c=None):
    """Devolve a lista de problemas. Lista vazia = pode publicar."""
    prob = []
    if EM is not None:
        falt = [k for k, v in EM.items() if v is None]
        if falt:
            prob.append(f"{len(falt)} listas de emendas não foram lidas (ex.: {falt[:3]})")
        incomp = [k for k, v in EM.items() if v is not None and len(v[1]) != v[0]]
        if incomp:
            prob.append(f"{len(incomp)} listas de emendas vieram incompletas (ex.: {incomp[:3]})")
    if CUS is not None:
        falt = [k for k, v in CUS.items() if v is None]
        if falt:
            prob.append(f"custos não lidos em {len(falt)} páginas (ex.: {falt[:3]})")
    if stats_c is not None and stats_c["dep_com_custo"] < 480:
        prob.append(f"só {stats_c['dep_com_custo']} deputados com custo do mandato (esperado perto de 513)")
    if CAM is None:
        prob.append("não foi possível ler o gasto da Câmara na página de transparência")
    elif CAM["ano"] != int(atual["data"][-4:]) and not atual["data"].startswith(("01/01", "02/01", "03/01", "04/01", "05/01")):
        prob.append(f"a página de transparência mostra o gasto de {CAM['ano']}, não do ano atual")
    nd = len(deps)
    if not 505 <= nd <= 520:
        prob.append(f"lista de deputados em exercício com {nd} nomes (esperado perto de 513)")
    falt = [k for k, v in OFF.items() if v is None]
    if falt:
        prob.append(f"{len(falt)} páginas oficiais de deputado não foram lidas (ex.: {falt[:3]})")
    if stats_v["votacoes"] < 1000:
        prob.append(f"só {stats_v['votacoes']} votações nominais no plenário (esperado mais de 1.000)")
    for y in ANOS[:-1]:
        if stats_v["plen_por_ano"][str(y)] == 0:
            prob.append(f"nenhuma votação nominal no plenário em {y}")
    ruins = [p for p in stats_v["placar"] if not p[1]]
    if ruins:
        prob.append(f"{len(ruins)} placares não batem com o resultado oficial: {ruins[:3]}")
    if stats_p["propostas"] < 200000:
        prob.append(f"só {stats_p['propostas']} propostas (esperado mais de 200.000)")
    # total de propostas de cada deputado x página oficial
    ok = tot = 0
    for d in deps:
        pag = [OFF.get((d["id"], a)) for a in ANOS]
        if any(p is None for p in pag):
            continue
        soma = sum(p[0] for p in pag); arq = stats_p["tot_dep"].get(d["id"], 0)
        tot += 1
        ok += abs(soma - arq) <= max(10, 0.03 * soma)
    if tot and ok / tot < 0.9:
        prob.append(f"total de propostas bate com a página oficial em só {ok} de {tot} deputados")
    # comparação com a última versão publicada
    if anterior:
        ant = anterior.get("contagens", {})
        for chave_, v in atual["contagens"].items():
            a = ant.get(chave_)
            if isinstance(v, (int, float)) and isinstance(a, (int, float)) and a > 0 and v < 0.99 * a:
                prob.append(f"{chave_} caiu de {a} para {v} desde a última versão publicada")
        for y in ANOS[:-1]:
            a = ant.get(f"votacoes_{y}"); v = atual["contagens"].get(f"votacoes_{y}")
            if a and v is not None and v < a:
                prob.append(f"votações de {y} diminuíram de {a} para {v}")
    return prob


# ---------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local"); ap.add_argument("--coleta"); ap.add_argument("--deps")
    ap.add_argument("--salvar-coleta", dest="salvar_coleta"); ap.add_argument("--salvar-rep", dest="salvar_rep")
    ap.add_argument("--saida", default=os.path.join(RAIZ, "saida"))
    ap.add_argument("--dados", default=os.path.join(RAIZ, "dados"))
    a = ap.parse_args()
    hoje = dt.datetime.now(dt.timezone(dt.timedelta(hours=-3)))
    saida = a.saida
    os.makedirs(saida, exist_ok=True)
    rel = {"inicio": hoje.isoformat(), "site": SITE_URL}
    try:
        log("1/6 lista de deputados em exercício")
        if a.deps:
            deps = json.load(open(a.deps))
        else:
            deps = baixar.api("deputados?itens=1000&ordem=ASC&ordenarPor=nome")["dados"]
        deps = sorted(deps, key=lambda d: d["nome"])
        log(f"  {len(deps)} deputados")
        log("2/6 arquivos oficiais (votações e proposições)")
        meta = coletar_arquivos(a.dados, a.local)
        log("3/6 página oficial de cada deputado (presença e propostas)")
        if a.coleta:
            C = json.load(open(a.coleta))
            K = lambda s: tuple(int(x) for x in s.split("-"))
            OFF = {K(k): v for k, v in C["OFF"].items()}; CUS = {K(k): v for k, v in C["CUS"].items()}
            EM = {K(k): v for k, v in C["EM"].items()}; CAM = C["CAM"]
        else:
            OFF, CUS = coletar_paginas([d["id"] for d in deps])
        log(f"  {sum(v is not None for v in OFF.values())} de {len(OFF)} páginas lidas")
        if not a.coleta:
            log("   emendas ao Orçamento de cada deputado")
            EM = coletar_emendas([d["id"] for d in deps])
            log(f"  {sum(v is not None for v in EM.values())} de {len(EM)} listas de emendas lidas, {sum(len(v[1]) for v in EM.values() if v):,} emendas")
            CAM = coletar_gasto_camara()
            log(f"  gasto da Câmara: {CAM}")
        if a.salvar_coleta:
            S = lambda d: {f"{k[0]}-{k[1]}": v for k, v in d.items()}
            json.dump({"OFF": S(OFF), "CUS": S(CUS), "EM": S(EM), "CAM": CAM}, open(a.salvar_coleta, "w"), ensure_ascii=False)
        log("4/6 votações")
        titulos = json.load(open(os.path.join(RAIZ, "robo", "titulos_votacoes.json")))
        R, MX, DEPS, VY, VOT, COD, sv = calcular_votacoes(a.dados, deps, titulos)
        log(f"  {sv['votacoes']} votações nominais no plenário, {len(VOT)} votações principais divididas")
        log("5/6 propostas")
        RES, PP, sp = calcular_propostas(a.dados, deps, saida)
        log(f"  {sp['propostas']:,} propostas, {sp['assinaturas']:,} assinaturas dos deputados atuais")
        CU, EMD, NOTA, SET, sc = calcular_custos_emendas(deps, CUS, EM, OFF, DEPS, RES, saida)
        log(f"  custo dos mandatos: R$ {sc['custo_total']:,.2f}; emendas pagas: R$ {sc['emendas_pago']:,.2f}")
        atual = {"data": hoje.strftime("%d/%m/%Y"), "arquivos": meta,
                 "contagens": {"votacoes": sv["votacoes"], "votos": sv["votos"], "propostas": sp["propostas"],
                               "assinaturas": sp["assinaturas_todas"], **{f"votacoes_{y}": sv["plen_por_ano"][str(y)] for y in ANOS},
                               "paginas_lidas": sum(v is not None for v in OFF.values()),
                               "custo_mandatos": sc["custo_total"], "emendas_pagas": sc["emendas_pago"]}}
        anterior = None
        try:
            r = requests.get(SITE_URL + "manifest.json", timeout=30)
            if r.status_code == 200:
                anterior = r.json()
                log(f"  comparando com a versão publicada em {anterior.get('data')}")
        except Exception:
            log("  sem versão anterior publicada para comparar")
        log("6/6 conferência")
        prob = conferir(atual, anterior, OFF, deps, sv, sp, CUS, EM, CAM, sc)
        rel["problemas"] = prob
        rel["contagens"] = atual["contagens"]
        if prob:
            for p in prob:
                log("  PROBLEMA: " + p)
            raise SystemExit(2)
        log("  tudo conferido")
        OFFl = [[d["id"]] + sum([(OFF[(d["id"], y)] or [0] * 5) for y in ANOS], []) for d in deps]
        t = open(os.path.join(RAIZ, "modelo", "index.html"), encoding="utf-8").read()
        J = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        rep = {"__DEPS__": J(DEPS), "__VY__": J(VY), "__RES__": J(RES), "__OFF__": J(OFFl), "__PP__": J(PP), "__COD__": J(COD),
               "__VOT__": J(VOT), "__RANK__": J(R), "__MATRIX__": J(MX), "__DATA__": atual["data"],
               "__NPROP__": f"{sp['propostas']:,}".replace(",", "."), "__NASS__": f"{sp['assinaturas']:,}".replace(",", "."),
               "__NVOT__": f"{sv['votacoes']:,}".replace(",", "."), "__ULTIMA__": "/".join(R["meta"]["ultimaVotacao"][:10].split("-")[::-1]),
               "__N21__": str(len(VOT)), "__CUS__": J(CU), "__EMD__": J(EMD), "__NOTA__": J(NOTA), "__SET__": J(SET),
               "__SETT__": J(sc["setores"]), "__CAM__": J(CAM)}
        if a.salvar_rep:
            json.dump(rep, open(a.salvar_rep, "w"), ensure_ascii=False)
        for k, v in rep.items():
            if k not in t:
                raise RuntimeError(f"marcador {k} não está no modelo")
            t = t.replace(k, v)
        open(os.path.join(saida, "index.html"), "w", encoding="utf-8").write(t)
        json.dump(atual, open(os.path.join(saida, "manifest.json"), "w"), ensure_ascii=False, indent=1)
        open(os.path.join(saida, ".nojekyll"), "w").close()
        rel["ok"] = True
        log("PRONTO: site montado em " + saida)
    finally:
        rel["log"] = LOG
        json.dump(rel, open(os.path.join(RAIZ, "relatorio.json"), "w"), ensure_ascii=False, indent=1)
        resumo = os.environ.get("GITHUB_STEP_SUMMARY")
        if resumo:
            with open(resumo, "a") as f:
                f.write("## Robô do Placar da Câmara\n\n")
                f.write("**Resultado:** " + ("publicado ✅" if rel.get("ok") else "NÃO publicado: o site continua com a última versão boa ❌") + "\n\n")
                for p in rel.get("problemas", []):
                    f.write(f"- {p}\n")
                f.write("\n```\n" + "\n".join(LOG[-60:]) + "\n```\n")


if __name__ == "__main__":
    main()
