#!/usr/bin/env python3
"""
Minha Cidade em Números — protótipo local
=========================================

Painel que recebe um CEP e mostra gastos da prefeitura com saúde e educação,
IDEB, emendas PIX, focos de queimada e indicadores sociais, comparando a
cidade com as vizinhas da mesma Região Geográfica Imediata do IBGE.

Como usar
---------
    python minha_cidade.py            # abre http://localhost:8765 no navegador
    python minha_cidade.py --porta 9000

Na nuvem (Render, Railway etc.), o script lê a variável PORT, escuta em
0.0.0.0 e não tenta abrir navegador. Comando de início: python minha_cidade.py

Só usa a biblioteca padrão do Python 3.8+. Nenhuma chave de API é necessária.

Por que um servidor local?
--------------------------
Algumas APIs públicas (ex.: SICONFI/Tesouro) recusam chamadas feitas direto
do navegador (CORS). Este script serve a página e repassa as consultas para
uma lista fechada de domínios oficiais, com cache em memória de 6 horas.

Fontes: BrasilAPI, ViaCEP, IBGE (localidades, agregados e Cidades),
SICONFI/Tesouro Nacional (DCA Anexo I-E), TransfereGov (transferências
especiais) e INPE (focos de queimada, satélite de referência).
"""

import argparse
import http.server
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

DOMINIOS_PERMITIDOS = {
    "brasilapi.com.br",
    "viacep.com.br",
    "servicodados.ibge.gov.br",
    "apidatalake.tesouro.gov.br",
    "api.transferegov.gestao.gov.br",
    "dataserver-coids.inpe.br",
}
CACHE_SEGUNDOS = 6 * 3600
TIMEOUT = 40
USER_AGENT = "MinhaCidadeEmNumeros/1.0 (prototipo local)"

_cache = {}
_cache_lock = threading.Lock()
_siconfi_lock = threading.Lock()  # o SICONFI aceita 1 consulta por segundo
_siconfi_ultima = [0.0]


def _contexto_ssl():
    try:
        import certifi  # opcional; ajuda no macOS com Python do python.org
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CTX = _contexto_ssl()


def buscar(url):
    """Baixa a URL (com cache). Retorna (status, content_type, corpo)."""
    agora = time.time()
    with _cache_lock:
        item = _cache.get(url)
        if item and agora - item[0] < CACHE_SEGUNDOS:
            return item[1]

    host = urllib.parse.urlparse(url).hostname or ""
    if host == "apidatalake.tesouro.gov.br":
        with _siconfi_lock:
            folga = 1.05 - (time.time() - _siconfi_ultima[0])
            if folga > 0:
                time.sleep(folga)
            _siconfi_ultima[0] = time.time()

    pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(pedido, timeout=TIMEOUT, context=SSL_CTX) as r:
            resposta = (r.status, r.headers.get("Content-Type", "application/octet-stream"), r.read())
    except urllib.error.HTTPError as e:
        return (e.code, "text/plain; charset=utf-8", e.read() or str(e).encode())
    except ssl.SSLError as e:
        msg = ("Falha de certificado SSL ao acessar %s. No macOS, rode o arquivo "
               "'Install Certificates.command' da pasta do Python, ou instale: pip install certifi.\n%s" % (host, e))
        return (502, "text/plain; charset=utf-8", msg.encode())
    except Exception as e:  # rede, timeout etc.
        return (502, "text/plain; charset=utf-8", ("Erro ao acessar %s: %s" % (host, e)).encode())

    if resposta[0] == 200:
        with _cache_lock:
            _cache[url] = (agora, resposta)
    return resposta


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "MinhaCidade/1.0"

    def _enviar(self, status, tipo, corpo):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        alvo = urllib.parse.urlparse(self.path)
        if alvo.path == "/saude":  # verificação de vida usada pelo serviço de hospedagem
            self._enviar(200, "text/plain; charset=utf-8", b"ok")
            return
        if alvo.path in ("/", "/index.html"):
            self._enviar(200, "text/html; charset=utf-8", PAGINA.encode("utf-8"))
            return
        if alvo.path == "/p":
            url = urllib.parse.parse_qs(alvo.query).get("u", [""])[0]
            partes = urllib.parse.urlparse(url)
            if partes.scheme != "https" or partes.hostname not in DOMINIOS_PERMITIDOS:
                self._enviar(403, "text/plain; charset=utf-8", b"Dominio nao permitido.")
                return
            status, tipo, corpo = buscar(url)
            self._enviar(status, tipo, corpo)
            return
        self._enviar(404, "text/plain; charset=utf-8", b"Nao encontrado.")

    def log_message(self, formato, *args):
        if "/p?" in (args[0] if args else ""):
            return  # silencia o log das consultas de dados
        sys.stderr.write("  %s\n" % (formato % args))


def main():
    ap = argparse.ArgumentParser(description="Minha Cidade em Números (servidor local)")
    na_nuvem = "PORT" in os.environ  # Render, Railway, Fly etc. definem PORT
    ap.add_argument("--porta", type=int, default=int(os.environ.get("PORT", 8765)))
    ap.add_argument("--sem-navegador", action="store_true", help="não abre o navegador automaticamente")
    a = ap.parse_args()
    for fluxo in (sys.stdout, sys.stderr):  # evita erro de acentuação em consoles antigos do Windows
        try:
            fluxo.reconfigure(errors="replace")
        except Exception:
            pass

    host = "0.0.0.0" if na_nuvem else "127.0.0.1"
    srv = http.server.ThreadingHTTPServer((host, a.porta), Handler)
    endereco = "http://localhost:%d" % a.porta
    print("Minha Cidade em Números rodando em %s:%d  (Ctrl+C para encerrar)" % (host, a.porta), flush=True)
    if not (a.sem_navegador or na_nuvem):
        threading.Timer(0.8, lambda: webbrowser.open(endereco)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")


PAGINA = r'''<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Minha Cidade em Números</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>
<style>
/* Layout: boletim municipal — cabeçalho com busca, faixa da cidade, grade de fichas por tema, tabela comparativa com vizinhas */
:root{
  --bg:#eef2f1; --surface:#ffffff; --ink:#13201d; --muted:#5b6b67; --line:#d5dedb;
  --accent:#0d6b5c; --accent-soft:#d8ebe6; --fire:#b4570f; --fire-soft:#f6e3d2;
  --warn:#8a6a00; --bar:#9fbdb6; --focus:#0d6b5c;
  --display:"Bricolage Grotesque", "Segoe UI", system-ui, sans-serif;
  --body:"IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --mono:"IBM Plex Mono", ui-monospace, "Cascadia Mono", Menlo, monospace;
  color-scheme:light;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0f1715; --surface:#16211e; --ink:#e3ece9; --muted:#93a6a1; --line:#2a3a36;
  --accent:#5cc7b2; --accent-soft:#1d3a34; --fire:#f0a062; --fire-soft:#3a2617;
  --warn:#e3c45a; --bar:#3f6b62; --focus:#5cc7b2; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0f1715; --surface:#16211e; --ink:#e3ece9; --muted:#93a6a1; --line:#2a3a36;
  --accent:#5cc7b2; --accent-soft:#1d3a34; --fire:#f0a062; --fire-soft:#3a2617;
  --warn:#e3c45a; --bar:#3f6b62; --focus:#5cc7b2; color-scheme:dark}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--ink);font:15px/1.55 var(--body);
  padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.wrap{max-width:1120px;margin:0 auto;padding-block:28px 56px;display:flex;flex-direction:column;gap:28px}
h1,h2,h3{font-family:var(--display);text-wrap:balance;margin:0;line-height:1.1}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums}
.label{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600}

header.top{display:flex;flex-wrap:wrap;gap:16px 32px;align-items:flex-end;justify-content:space-between}
.brand h1{font-size:clamp(28px,4vw,40px);font-weight:700;letter-spacing:-.01em}
.brand p{margin:6px 0 0;color:var(--muted);max-width:52ch}
form.busca{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap}
form.busca label{display:flex;flex-direction:column;gap:4px}
form.busca input{font:500 18px var(--mono);width:calc(9ch + 28px);padding:10px 12px;border:1px solid var(--line);
  border-radius:6px;background:var(--surface);color:var(--ink)}
button{font:600 15px var(--body);padding:11px 18px;border-radius:6px;border:0;background:var(--accent);
  color:var(--surface);cursor:pointer}
button:hover{filter:brightness(1.08)}
button:disabled{opacity:.6;cursor:wait}

.cidade{display:flex;flex-wrap:wrap;gap:12px 40px;align-items:baseline;padding-bottom:18px;border-bottom:2px solid var(--ink)}
.cidade h2{font-size:clamp(36px,6vw,64px);font-weight:700;letter-spacing:-.02em}
.cidade h2 small{font-size:.42em;color:var(--muted);font-weight:500;margin-left:.3em}
.fatos{display:flex;flex-wrap:wrap;gap:8px 28px}
.fato{display:flex;flex-direction:column}
.fato b{font:500 18px var(--mono)}
.msg{padding:12px 14px;border-radius:6px;background:var(--fire-soft);color:var(--ink)}

.grade{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.ficha{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:18px;display:flex;flex-direction:column;gap:12px;min-width:0}
.ficha h3{font-size:18px;font-weight:700}
.ficha .topo{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.fonte{font-size:12px;color:var(--muted)}
.grande{font:500 32px var(--mono);letter-spacing:-.02em;line-height:1}
.grande small{font-size:14px;color:var(--muted);margin-left:4px;letter-spacing:0}
.sub{color:var(--muted);font-size:13.5px}
.carregando{color:var(--muted);font-size:13.5px}
.carregando::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--accent);margin-right:8px;animation:pulsa 1s infinite alternate}
@keyframes pulsa{from{opacity:.25}to{opacity:1}}
@media (prefers-reduced-motion:reduce){.carregando::before{animation:none}}
.indisp{color:var(--muted);font-size:13.5px}

.dupla{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.ranking{display:flex;flex-direction:column;gap:5px;font-size:13px}
.rk{display:grid;grid-template-columns:minmax(0,9.5em) 1fr auto;gap:8px;align-items:center}
.rk span:first-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rk .trilho{height:8px;background:var(--accent-soft);border-radius:2px;overflow:hidden}
.rk .trilho i{display:block;height:100%;background:var(--bar)}
.rk.eu{font-weight:600}
.rk.eu .trilho i{background:var(--accent)}
.fogo .rk .trilho{background:var(--fire-soft)} .fogo .rk .trilho i{background:color-mix(in srgb,var(--fire) 45%,var(--surface))}
.fogo .rk.eu .trilho i{background:var(--fire)}
.fogo .grande{color:var(--fire)}
.pill{display:inline-block;font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;background:var(--accent-soft);color:var(--accent)}
.pill.alta{background:var(--fire-soft);color:var(--fire)}
svg.spark{width:100%;height:48px;display:block;margin-top:6px}
.eixo{display:flex;justify-content:space-between;font:10px var(--mono);color:var(--muted);margin-top:2px}
.eixo.meses{display:grid;grid-template-columns:repeat(12,1fr);text-align:center}
.lista{margin:0;padding:0;list-style:none;font-size:13.5px;display:flex;flex-direction:column;gap:4px}
.lista li{display:flex;justify-content:space-between;gap:12px}

section.comp h2{font-size:24px;margin-bottom:6px}
section.comp p{margin:0 0 12px;color:var(--muted);max-width:70ch}
.tabela{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--surface)}
table{border-collapse:collapse;width:100%;min-width:860px;font-size:14px}
th,td{padding:9px 12px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
thead th{font-size:12px;color:var(--muted);font-weight:600;cursor:pointer;user-select:none;background:var(--surface);position:sticky;top:0}
thead th:hover{color:var(--ink)}
thead th[aria-sort]::after{content:" ↓"} thead th[aria-sort="ascending"]::after{content:" ↑"}
td{font-family:var(--mono);font-variant-numeric:tabular-nums}
td:first-child{font-family:var(--body)}
tr.eu td{background:var(--accent-soft);font-weight:600}
td sup{font-size:10px;color:var(--muted);margin-left:3px;font-weight:400}
section.comp p#nota-tab{margin:8px 0 0}
tbody tr:last-child td{border-bottom:0}
footer{font-size:13px;color:var(--muted);display:flex;flex-direction:column;gap:6px;border-top:1px solid var(--line);padding-top:16px}
footer p{margin:0;max-width:90ch}
@media (max-width:520px){.dupla{grid-template-columns:1fr}.grande{font-size:28px}}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div class="brand">
      <h1>Minha Cidade em Números</h1>
      <p>Digite um CEP e veja quanto a prefeitura gasta com saúde e educação, como estão as escolas, quanto chegou em emendas e quantos focos de queimada houve, lado a lado com as cidades vizinhas.</p>
    </div>
    <form class="busca" id="busca">
      <label for="cep"><span class="label">CEP</span>
        <input id="cep" inputmode="numeric" autocomplete="postal-code" placeholder="00000-000" value="80010-000" maxlength="9" required>
      </label>
      <button id="ir" type="submit">Consultar</button>
    </form>
  </header>

  <div id="erro" class="msg" hidden></div>

  <section class="cidade" id="cidade" aria-live="polite">
    <h2 id="nome">Carregando…</h2>
    <div class="fatos" id="fatos"></div>
  </section>

  <div class="grade">
    <article class="ficha" id="f-saude"><div class="topo"><h3>Saúde</h3><span class="fonte">SICONFI · DCA</span></div><div class="corpo"><p class="carregando">Buscando gastos da prefeitura…</p></div></article>
    <article class="ficha" id="f-educ"><div class="topo"><h3>Educação</h3><span class="fonte">SICONFI · DCA</span></div><div class="corpo"><p class="carregando">Buscando gastos da prefeitura…</p></div></article>
    <article class="ficha" id="f-ideb"><div class="topo"><h3>IDEB da rede pública</h3><span class="fonte">INEP via IBGE Cidades</span></div><div class="corpo"><p class="carregando">Buscando notas…</p></div></article>
    <article class="ficha" id="f-emendas"><div class="topo"><h3>Emendas PIX</h3><span class="fonte">TransfereGov</span></div><div class="corpo"><p class="carregando">Buscando transferências…</p></div></article>
    <article class="ficha fogo" id="f-focos"><div class="topo"><h3>Focos de queimada</h3><span class="fonte">INPE</span></div><div class="corpo"><p class="carregando">Baixando focos do estado…</p></div></article>
    <article class="ficha" id="f-social"><div class="topo"><h3>Renda e saúde pública</h3><span class="fonte">IBGE Cidades</span></div><div class="corpo"><p class="carregando">Buscando indicadores…</p></div></article>
  </div>

  <section class="comp">
    <h2>Comparação com as vizinhas</h2>
    <p id="comp-desc">Municípios da mesma Região Geográfica Imediata do IBGE. Clique no título de uma coluna para ordenar.</p>
    <div class="tabela"><table id="tab"><thead></thead><tbody></tbody></table></div>
    <p class="fonte" id="nota-tab"></p>
  </section>

  <footer>
    <p><b>Segurança pública:</b> a API do Atlas da Violência (Ipea), usada pelo mcp-brasil, foi desativada na reformulação do site e ainda não tem substituto aberto por município. Consulte o <a href="https://www.ipea.gov.br/atlasviolencia/" target="_blank" rel="noopener">Atlas da Violência</a> diretamente.</p>
    <p>Fontes: <a href="https://brasilapi.com.br" target="_blank" rel="noopener">BrasilAPI</a> · <a href="https://servicodados.ibge.gov.br/api/docs" target="_blank" rel="noopener">IBGE</a> · <a href="https://apidatalake.tesouro.gov.br/docs/siconfi/" target="_blank" rel="noopener">SICONFI/Tesouro</a> · <a href="https://api.transferegov.gestao.gov.br/transferenciasespeciais/" target="_blank" rel="noopener">TransfereGov</a> · <a href="https://terrabrasilis.dpi.inpe.br/queimadas/portal/" target="_blank" rel="noopener">INPE Queimadas</a>. Gastos por função são despesas empenhadas declaradas pela prefeitura no ano indicado. Dados públicos, sem garantia; confira na fonte antes de usar em decisões.</p>
  </footer>
</div>

<script>
"use strict";
// ---------- acesso às APIs ----------
// Quando a página vem do minha_cidade.py, tudo passa pelo proxy /p, que evita bloqueios de CORS.
const VIA_PROXY = /^https?:$/.test(location.protocol); // servido pelo minha_cidade.py (local ou na nuvem)
const rota = url => VIA_PROXY ? "/p?u=" + encodeURIComponent(url) : url;
async function req(url, tipo = "json", tentativas = 2) {
  for (let i = 0; ; i++) {
    try {
      const r = await fetch(rota(url));
      if (!r.ok) throw new Error("HTTP " + r.status);
      return tipo === "json" ? await r.json() : tipo === "buf" ? await r.arrayBuffer() : await r.text();
    } catch (e) {
      if (i >= tentativas) throw e;
      await espera(800 * (i + 1));
    }
  }
}
const espera = ms => new Promise(r => setTimeout(r, ms));

// ---------- utilidades ----------
const $ = s => document.querySelector(s);
const sem = s => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase().trim();
const nf0 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const reais = v => "R$ " + nf0.format(v);
function reaisCurto(v) {
  if (v >= 1e9) return "R$ " + nf1.format(v / 1e9) + " bi";
  if (v >= 1e6) return "R$ " + nf1.format(v / 1e6) + " mi";
  if (v >= 1e3) return "R$ " + nf0.format(v / 1e3) + " mil";
  return reais(v);
}
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ultimo = res => { // {ano: "valor"} -> [ano, número] do ano mais recente com valor
  const anos = Object.keys(res || {}).filter(a => res[a] !== null && res[a] !== "" && !isNaN(parseFloat(res[a]))).sort();
  if (!anos.length) return null;
  const a = anos[anos.length - 1]; return [a, parseFloat(res[a])];
};

// ---------- estado ----------
let S = null, rodada = 0;
const IND = { ideb_ai: 78187, ideb_af: 78192, pib_pc: 47001, mort: 30279, salario: 29765, esgoto: 60030 };

function corpo(id, html) { $("#" + id + " .corpo").innerHTML = html; }
function indisponivel(id, motivo) { corpo(id, `<p class="indisp">${esc(motivo)}</p>`); }

function ranking(campo, fmt, opts = {}) {
  const linhas = S.muns.filter(m => m[campo] != null).sort((a, b) => b[campo] - a[campo]);
  if (!linhas.length) return "";
  const max = Math.max(...linhas.map(m => m[campo])) || 1;
  return `<div class="ranking" role="list">${linhas.map(m => `
    <div class="rk${m.id === S.id ? " eu" : ""}" role="listitem"><span title="${esc(m.nome)}">${esc(m.nome)}</span>
    <span class="trilho"><i style="width:${(m[campo] / max * 100).toFixed(1)}%"></i></span>
    <span class="num">${fmt(m[campo])}</span></div>`).join("")}</div>`;
}
function posicao(campo, maiorMelhor = true) {
  const v = S.muns.filter(m => m[campo] != null).sort((a, b) => maiorMelhor ? b[campo] - a[campo] : a[campo] - b[campo]);
  const i = v.findIndex(m => m.id === S.id);
  return i < 0 ? "" : `${i + 1}º de ${v.length} na região`;
}
function spark(pares, cor = "var(--accent)") {
  if (pares.length < 2) return "";
  const W = 300, H = 48, P = 5;
  const ys = pares.map(p => p[1]), lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const x = i => P + i * (W - 2 * P) / (pares.length - 1), y = v => H - P - (v - lo) / span * (H - 2 * P);
  const pts = pares.map((p, i) => `${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const L = pares.length - 1;
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Série histórica de ${pares[0][0]} a ${pares[L][0]}">
    <polyline points="${pts}" fill="none" stroke="${cor}" stroke-width="2" vector-effect="non-scaling-stroke"/>
    <circle cx="${x(L)}" cy="${y(pares[L][1])}" r="3.5" fill="${cor}"/></svg>
    <div class="eixo"><span>${pares[0][0]}</span><span>${pares[L][0]}</span></div>`;
}

// ---------- 1. localização ----------
async function localizar(cep) {
  let c;
  try { c = await req(`https://brasilapi.com.br/api/cep/v1/${cep}`, "json", 0); }
  catch (e) { // CEP genérico de cidade às vezes só existe no ViaCEP
    const v = await req(`https://viacep.com.br/ws/${cep}/json/`);
    if (v.erro) throw new Error("CEP inexistente");
    c = { city: v.localidade, state: v.uf, neighborhood: v.bairro, ibge: { city: v.ibge } };
  }
  let id = c.ibge && c.ibge.city;
  if (!id) { // nem todo provedor de CEP devolve o código IBGE
    const lista = await req(`https://servicodados.ibge.gov.br/api/v1/localidades/estados/${c.state}/municipios`);
    const achou = lista.find(m => sem(m.nome) === sem(c.city));
    if (!achou) throw new Error("Não encontrei o município do CEP no IBGE.");
    id = String(achou.id);
  }
  const mun = await req(`https://servicodados.ibge.gov.br/api/v1/localidades/municipios/${id}`);
  const ri = mun["regiao-imediata"];
  const regiao = await req(`https://servicodados.ibge.gov.br/api/v1/localidades/regioes-imediatas/${ri.id}/municipios`);
  const ids = regiao.map(m => m.id);
  const pop = await req(`https://servicodados.ibge.gov.br/api/v3/agregados/6579/periodos/-1/variaveis/9324?localidades=N6[${ids.join(",")}]`);
  const serie = pop[0].resultados[0].series, popPor = {}; let anoPop = "";
  serie.forEach(s => { const [a, v] = Object.entries(s.serie)[0]; popPor[s.localidade.id] = +v; anoPop = a; });
  let muns = regiao.map(m => ({ id: String(m.id), nome: m.nome, pop: popPor[m.id] ?? null }));
  const eu = muns.find(m => m.id === String(id));
  const outros = muns.filter(m => m.id !== String(id)).sort((a, b) => (b.pop || 0) - (a.pop || 0));
  muns = [eu, ...outros.slice(0, 8)];
  return { cep, id: String(id), nome: mun.nome, uf: mun.microrregiao.mesorregiao.UF.sigla, ufNome: mun.microrregiao.mesorregiao.UF.nome,
    regiao: ri.nome, nRegiao: regiao.length, anoPop, bairro: c.neighborhood, muns };
}

// ---------- 2. indicadores IBGE (IDEB, PIB, mortalidade, salário, esgoto) ----------
async function carregarIBGE(r) {
  const locs = S.muns.map(m => m.id.slice(0, 6)).join("|");
  const dados = await req(`https://servicodados.ibge.gov.br/api/v1/pesquisas/indicadores/${Object.values(IND).join("|")}/resultados/${locs}`);
  if (r !== rodada) return;
  const porId = Object.fromEntries(dados.map(d => [d.id, d.res]));
  S.ano = {};
  for (const [chave, cod] of Object.entries(IND)) {
    for (const linha of porId[cod] || []) {
      const m = S.muns.find(x => x.id.startsWith(linha.localidade)); if (!m) continue;
      const u = ultimo(linha.res); if (!u) continue;
      m[chave] = u[1]; S.ano[chave] = u[0];
      if (m.id === S.id && chave.startsWith("ideb")) m[chave + "_serie"] = Object.entries(linha.res).filter(([, v]) => v).map(([a, v]) => [a, +v]);
    }
  }
  const eu = S.muns[0];
  if (eu.ideb_ai == null && eu.ideb_af == null) indisponivel("f-ideb", "O INEP não divulgou IDEB da rede pública para este município.");
  else corpo("f-ideb", `<div class="dupla">
      <div><span class="label">Anos iniciais</span><div class="grande">${eu.ideb_ai != null ? nf1.format(eu.ideb_ai) : "—"}</div><div class="sub">${posicao("ideb_ai")}</div>${spark(eu.ideb_ai_serie || [])}</div>
      <div><span class="label">Anos finais</span><div class="grande">${eu.ideb_af != null ? nf1.format(eu.ideb_af) : "—"}</div><div class="sub">${posicao("ideb_af")}</div>${spark(eu.ideb_af_serie || [])}</div>
    </div><div class="sub">Nota de 0 a 10, edição ${S.ano.ideb_ai || S.ano.ideb_af}. Referência: o PDE fixou 6,0 como meta nacional para os anos iniciais.</div>`);
  corpo("f-social", `<ul class="lista">
      <li><span>PIB per capita (${S.ano.pib_pc || "—"})</span><b class="num">${eu.pib_pc != null ? reais(eu.pib_pc) : "—"}</b></li>
      <li><span>Salário médio formal (${S.ano.salario || "—"})</span><b class="num">${eu.salario != null ? nf1.format(eu.salario) + " sal. mín." : "—"}</b></li>
      <li><span>Mortalidade infantil (${S.ano.mort || "—"})</span><b class="num">${eu.mort != null ? nf1.format(eu.mort) + " por mil" : "—"}</b></li>
      <li><span>Esgotamento sanitário adequado (${S.ano.esgoto || "—"})</span><b class="num">${eu.esgoto != null ? nf1.format(eu.esgoto) + "%" : "—"}</b></li>
    </ul><div class="sub">PIB per capita: ${posicao("pib_pc")}.</div>`);
  tabela();
}

// ---------- 3. gastos com saúde e educação (SICONFI, 1 consulta por segundo) ----------
async function dca(id, ano) {
  const j = await req(`https://apidatalake.tesouro.gov.br/ords/siconfi/tt/dca?an_exercicio=${ano}&id_ente=${id}&no_anexo=DCA-Anexo%20I-E`);
  const it = (j.items || []).filter(i => i.coluna === "Despesas Empenhadas");
  if (!it.length) return null;
  const tot = it.filter(i => /Total Geral/i.test(i.rotulo || ""));
  const base = tot.length ? tot : it;
  const v = rx => (base.find(i => rx.test(i.conta)) || {}).valor ?? null;
  return { ano, saude: v(/^10 - /), educ: v(/^12 - /), total: v(/Exceto Intra/i), pop: it[0].populacao };
}
async function carregarFiscal(r) {
  const anoAtual = new Date().getFullYear();
  let anoRef = null;
  for (const m of S.muns) {
    if (r !== rodada) return;
    let d = null;
    try {
      for (const ano of anoRef ? [anoRef, anoRef - 1] : [anoAtual - 1, anoAtual - 2]) {
        d = await dca(m.id, ano); await espera(1100);
        if (d) break;
      }
    } catch (e) { d = null; }
    if (r !== rodada) return;
    if (d) {
      if (!anoRef) anoRef = d.ano;
      const pop = d.pop || m.pop;
      m.saude_pc = d.saude != null && pop ? d.saude / pop : null;
      m.educ_pc = d.educ != null && pop ? d.educ / pop : null;
      m.fiscal_ano = d.ano;
      if (m.id === S.id) S.dca = d;
    }
    m.fiscal_ok = true;
    fichasFiscais(); tabela();
  }
}
function fichasFiscais() {
  const d = S.dca, eu = S.muns[0], pend = S.muns.filter(m => !m.fiscal_ok).length;
  const rodape = pend ? `<p class="carregando">Comparando com as vizinhas (faltam ${pend})…</p>` : "";
  if (!eu.fiscal_ok) return;
  if (!d) { ["f-saude", "f-educ"].forEach(f => indisponivel(f, "A prefeitura não entregou a Declaração de Contas Anuais dos últimos dois anos ao Tesouro.")); return; }
  const ficha = (f, campo, total, piso) => corpo(f, `
    <div><div class="grande">${eu[campo] != null ? reais(eu[campo]) : "—"}<small>por habitante</small></div>
    <div class="sub">${reaisCurto(total)} empenhados em ${d.ano}${d.total ? ` · ${nf1.format(total / d.total * 100)}% da despesa total` : ""}${piso}</div></div>
    <div class="sub">${posicao(campo)}</div>${ranking(campo, v => reais(v))}${rodape}`);
  ficha("f-saude", "saude_pc", d.saude, "");
  ficha("f-educ", "educ_pc", d.educ, "");
}

// ---------- 4. emendas PIX (TransfereGov) ----------
async function carregarEmendas(r) {
  const nomes = S.muns.map(m => "MUNICIPIO DE " + sem(m.nome));
  const filtro = "in.(" + nomes.map(n => `"${n}"`).join(",") + ")";
  const url = "https://api.transferegov.gestao.gov.br/transferenciasespeciais/plano_acao_especial?select=ano_plano_acao,valor_custeio_plano_acao,valor_investimento_plano_acao,nome_parlamentar_emenda_plano_acao,nome_beneficiario_plano_acao"
    + "&uf_beneficiario_plano_acao=eq." + S.uf + "&nome_beneficiario_plano_acao=" + encodeURIComponent(filtro) + "&limit=10000";
  const linhas = await req(url);
  if (r !== rodada) return;
  const porNome = {};
  linhas.forEach(l => {
    const v = (+l.valor_custeio_plano_acao || 0) + (+l.valor_investimento_plano_acao || 0);
    const k = l.nome_beneficiario_plano_acao; (porNome[k] = porNome[k] || []).push({ ...l, v });
  });
  S.muns.forEach((m, i) => {
    const ls = porNome[nomes[i]] || [];
    m.emendas = ls.reduce((s, l) => s + l.v, 0);
    m.emendas_pc = m.pop ? m.emendas / m.pop : null;
    if (m.id === S.id) m.emendas_linhas = ls;
  });
  const eu = S.muns[0], ls = eu.emendas_linhas;
  const anos = ls.map(l => l.ano_plano_acao);
  const periodo = anos.length ? `${Math.min(...anos)}–${Math.max(...anos)}` : "";
  const porParl = {};
  ls.forEach(l => porParl[l.nome_parlamentar_emenda_plano_acao] = (porParl[l.nome_parlamentar_emenda_plano_acao] || 0) + l.v);
  const top = Object.entries(porParl).sort((a, b) => b[1] - a[1]).slice(0, 4);
  corpo("f-emendas", !ls.length ? `<div class="grande">R$ 0</div><p class="indisp">Nenhuma transferência especial encontrada para a prefeitura.</p>${ranking("emendas_pc", v => reais(v) + "/hab")}` : `
    <div><div class="grande">${reaisCurto(eu.emendas)}<small>${periodo}</small></div>
    <div class="sub">${ls.length} planos de ação · ${eu.emendas_pc != null ? reais(eu.emendas_pc) + " por habitante" : ""}</div></div>
    <div><span class="label">Quem mais enviou</span><ul class="lista">${top.map(([n, v]) => `<li><span>${esc(n)}</span><b class="num">${reaisCurto(v)}</b></li>`).join("")}</ul></div>
    <div><span class="label">Por habitante na região</span>${ranking("emendas_pc", v => reais(v))}</div>`);
  tabela();
}

// ---------- 5. focos de queimada (INPE, arquivo anual por estado) ----------
async function carregarFocos(r) {
  const base = `https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/anual/EstadosBr_sat_ref/${S.uf}/`;
  const lista = await req(base, "text");
  const anos = [...lista.matchAll(/focos_br_[a-z]{2}_ref_(\d{4})\.zip/g)].map(m => +m[1]);
  const ultimos = [...new Set(anos)].sort((a, b) => b - a).slice(0, 2);
  if (!ultimos.length) throw new Error("sem arquivos");
  const contar = async ano => {
    const z = await JSZip.loadAsync(await req(`${base}focos_br_${S.uf.toLowerCase()}_ref_${ano}.zip`, "buf"));
    const csv = await z.file(Object.keys(z.files).find(f => f.endsWith(".csv"))).async("string");
    const ln = csv.split(/\r?\n/), cab = ln[0].split(",");
    const iMun = cab.indexOf("municipio"), iData = cab.findIndex(c => /^data/.test(c));
    const cont = {}, mes = {};
    for (let i = 1; i < ln.length; i++) {
      const c = ln[i].split(","); if (c.length <= iMun) continue;
      const k = sem(c[iMun]); cont[k] = (cont[k] || 0) + 1;
      const mm = (c[iData] || "").trim().slice(5, 7); (mes[k] = mes[k] || Array(12).fill(0))[+mm - 1]++;
    }
    return { ano, cont, mes };
  };
  const [atual, ant] = await Promise.all(ultimos.map(contar));
  if (r !== rodada) return;
  S.focosAno = atual.ano;
  S.muns.forEach(m => { m.focos = atual.cont[sem(m.nome)] || 0; m.focos_ant = ant ? (ant.cont[sem(m.nome)] || 0) : null; });
  const eu = S.muns[0], meses = atual.mes[sem(eu.nome)] || Array(12).fill(0);
  const varia = eu.focos_ant ? (eu.focos - eu.focos_ant) / eu.focos_ant * 100 : null;
  const maxM = Math.max(...meses, 1);
  const barras = `<svg class="spark" viewBox="0 0 300 48" preserveAspectRatio="none" role="img" aria-label="Focos por mês em ${atual.ano}">
    ${meses.map((v, i) => `<rect x="${i * 25 + 3}" y="${46 - v / maxM * 44}" width="19" height="${Math.max(v / maxM * 44, v ? 1.5 : 0.8)}" fill="var(--fire)" opacity="${v ? 1 : .25}"/>`).join("")}
    </svg><div class="eixo meses">${["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"].map(l => `<span>${l}</span>`).join("")}</div>`;
  corpo("f-focos", `<div><div class="grande">${nf0.format(eu.focos)}<small>${eu.focos === 1 ? "foco" : "focos"} em ${atual.ano}</small></div>
    <div class="sub">${varia == null ? "" : `<span class="pill${varia > 0 ? " alta" : ""}">${varia > 0 ? "+" : ""}${nf0.format(varia)}% vs ${ant.ano}</span> `}${posicao("focos")}</div></div>
    ${barras}${ranking("focos", v => nf0.format(v))}
    <div class="sub">Satélite de referência do INPE; ano mais recente publicado para ${esc(S.ufNome)}.</div>`);
  tabela();
}

// ---------- tabela comparativa ----------
const COLS = [
  { k: "nome", t: "Município", f: v => esc(v) },
  { k: "pop", t: "População", f: v => nf0.format(v) },
  { k: "saude_pc", t: "Saúde R$/hab", f: v => nf0.format(v) },
  { k: "educ_pc", t: "Educação R$/hab", f: v => nf0.format(v) },
  { k: "ideb_ai", t: "IDEB iniciais", f: v => nf1.format(v) },
  { k: "ideb_af", t: "IDEB finais", f: v => nf1.format(v) },
  { k: "pib_pc", t: "PIB per capita", f: v => nf0.format(v) },
  { k: "emendas_pc", t: "Emendas R$/hab", f: v => nf0.format(v) },
  { k: "focos", t: "Focos", f: v => nf0.format(v) },
];
let ordem = { k: "pop", asc: false };
function tabela() {
  if (!S) return;
  $("#tab thead").innerHTML = "<tr>" + COLS.map(c => `<th scope="col" data-k="${c.k}" tabindex="0"${ordem.k === c.k ? ` aria-sort="${ordem.asc ? "ascending" : "descending"}"` : ""}>${c.t}${c.k === "focos" && S.focosAno ? " " + S.focosAno : ""}</th>`).join("") + "</tr>";
  const linhas = [...S.muns].sort((a, b) => {
    const x = a[ordem.k], y = b[ordem.k];
    if (x == null) return 1; if (y == null) return -1;
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * (ordem.asc ? 1 : -1);
  });
  const anoRef = S.dca && S.dca.ano;
  const marca = (m, k) => (k === "saude_pc" || k === "educ_pc") && m[k] != null && anoRef && m.fiscal_ano !== anoRef ? `<sup title="Dado de ${m.fiscal_ano}">${m.fiscal_ano}</sup>` : "";
  $("#tab tbody").innerHTML = linhas.map(m => `<tr${m.id === S.id ? ' class="eu"' : ""}>` + COLS.map(c => `<td>${m[c.k] != null ? c.f(m[c.k]) + marca(m, c.k) : "—"}</td>`).join("") + "</tr>").join("");
  $("#nota-tab").textContent = anoRef ? `Gastos de ${anoRef}; números pequenos ao lado indicam outro ano, quando a prefeitura ainda não entregou as contas de ${anoRef}. "—" significa dado não declarado ou não divulgado.` : "";
}
function ordenar(k) { ordem = { k, asc: ordem.k === k ? !ordem.asc : k === "nome" }; tabela(); }
$("#tab thead").addEventListener("click", e => { const th = e.target.closest("th"); if (th) ordenar(th.dataset.k); });
$("#tab thead").addEventListener("keydown", e => { const th = e.target.closest("th"); if (th && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); ordenar(th.dataset.k); } });

// ---------- orquestração ----------
const FICHAS = { "f-saude": "Buscando gastos da prefeitura…", "f-educ": "Buscando gastos da prefeitura…", "f-ideb": "Buscando notas…", "f-emendas": "Buscando transferências…", "f-focos": "Baixando focos do estado…", "f-social": "Buscando indicadores…" };
async function consultar(cepBruto) {
  const cep = cepBruto.replace(/\D/g, "");
  if (cep.length !== 8) { mostrarErro("O CEP precisa ter 8 dígitos."); return; }
  const r = ++rodada;
  $("#erro").hidden = true; $("#ir").disabled = true;
  $("#nome").textContent = "Localizando CEP…"; $("#fatos").innerHTML = "";
  Object.entries(FICHAS).forEach(([f, t]) => corpo(f, `<p class="carregando">${t}</p>`));
  $("#tab tbody").innerHTML = "";
  try {
    S = await localizar(cep);
  } catch (e) {
    if (r === rodada) { mostrarErro("Não consegui localizar esse CEP. Confira os números e tente de novo."); $("#nome").textContent = "—"; $("#ir").disabled = false; }
    return;
  }
  if (r !== rodada) return;
  const eu = S.muns[0];
  $("#nome").innerHTML = `${esc(S.nome)}<small>${S.uf}</small>`;
  $("#fatos").innerHTML = [
    ["População " + S.anoPop, eu.pop != null ? nf0.format(eu.pop) : "—"],
    ["Região imediata", esc(S.regiao)],
    ["Vizinhas comparadas", `${S.muns.length - 1} de ${S.nRegiao - 1}`],
    ["CEP", S.cep.replace(/(\d{5})(\d{3})/, "$1-$2")],
  ].map(([l, v]) => `<div class="fato"><span class="label">${l}</span><b>${v}</b></div>`).join("");
  $("#comp-desc").textContent = `${S.nome} e as ${S.muns.length - 1} maiores cidades da Região Geográfica Imediata de ${S.regiao} (IBGE). Clique no título de uma coluna para ordenar.`;
  tabela();
  $("#ir").disabled = false;
  const falha = (f, txt) => e => { if (r === rodada) indisponivel(f, txt); };
  carregarIBGE(r).catch(e => { falha("f-ideb", "O IBGE não respondeu. Tente de novo em instantes.")(e); falha("f-social", "O IBGE não respondeu. Tente de novo em instantes.")(e); });
  carregarEmendas(r).catch(falha("f-emendas", "O TransfereGov não respondeu. Tente de novo em instantes."));
  carregarFocos(r).catch(falha("f-focos", "Não consegui baixar os focos do INPE para este estado."));
  carregarFiscal(r).catch(e => { falha("f-saude", "O SICONFI não respondeu.")(e); falha("f-educ", "O SICONFI não respondeu.")(e); });
}
function mostrarErro(t) { const e = $("#erro"); e.textContent = t; e.hidden = false; }
$("#cep").addEventListener("input", e => { const d = e.target.value.replace(/\D/g, "").slice(0, 8); e.target.value = d.length > 5 ? d.slice(0, 5) + "-" + d.slice(5) : d; });
$("#busca").addEventListener("submit", e => { e.preventDefault(); consultar($("#cep").value); });
const inicial = new URLSearchParams(location.search).get("cep");
if (inicial) $("#cep").value = inicial;
consultar($("#cep").value);
</script>
</body>
</html>
'''

if __name__ == "__main__":
    main()
