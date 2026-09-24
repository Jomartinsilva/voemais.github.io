#!/usr/bin/env python3
"""
Verificador de seguranca do site Voe Mais.
Roda em 3 lugares: (1) manualmente, (2) dentro do subir_banners.ps1 antes do commit,
(3) no GitHub Actions a cada push e toda semana.

Uso:  python scripts/verificar_seguranca.py [pasta-do-repo]
Saida: 0 = ok (pode ter avisos) | 1 = FALHA (nao publique)

REGRA: este arquivo e a lista PERMITIDOS abaixo so podem ser alterados com autorizacao
explicita do Josimar. Ver a secao SEGURANCA de 07_SITE_VOEMAIS/01_CONTEXTO/PROMPT_CLAUDE_CODE.md.
"""
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

# ----------------------------------------------------------------------------
# LISTA DE PERMITIDOS  (o que o site tem direito de exibir/apontar)
# ----------------------------------------------------------------------------
PERMITIDOS = {
    "whatsapp": {"5531983097640", "5531983002955"},      # principal e suporte
    "emails": {"travel.voemais@gmail.com"},
    "dominios_links": {"wa.me", "www.instagram.com", "instagram.com"},
    "hosts_em_texto": {"wa.me", "www.instagram.com", "instagram.com", "www.w3.org",
                       "jomartinsilva.github.io"},        # qualquer URL escrita no codigo
    "hosts_script": set(),                                 # scripts externos: nenhum (exigiria SRI)
    # Relé de cotação (Cloudflare Worker) que envia o pedido ao Telegram. Vazio = envio desativado (usa WhatsApp).
    # Para ativar: incluir aqui o host do Worker (ex.: "cotacao-voemais.conta.workers.dev"), só com autorização do Josimar.
    "hosts_connect": set(),
}
EXT_IMAGEM = {".jpg", ".jpeg", ".png", ".webp"}
EXT_PROIBIDAS = {".exe", ".dll", ".msi", ".scr", ".jar", ".php", ".com", ".vbs", ".apk", ".cmd"}
IGNORAR_NOMES = {"thumbs.db", "desktop.ini", ".ds_store"}
IGNORAR_PASTAS = {".git", "node_modules", "__pycache__"}
EXT_TEXTO = {".html", ".htm", ".js", ".json", ".md", ".yml", ".yaml", ".txt", ".css", ".xml",
             ".ps1", ".bat", ".py", ".toml", ".cfg", ".ini"}

SEGREDOS = [
    ("chave AWS", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("token GitHub", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("chave Google", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("chave Stripe", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{16,}")),
    ("token Slack", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}")),
    ("token de bot do Telegram", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("chave privada", re.compile("-----BEGIN " + r"(?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("segredo em texto", re.compile(
        r"""(?i)\b(api[_-]?key|secret|token|passw(?:or)?d|senha)\b["']?\s*[:=]\s*["'][^"'\s]{8,}["']""")),
]

falhas, avisos = [], []


def falha(arq, msg, linha=None):
    falhas.append(f"{arq}{':' + str(linha) if linha else ''}  {msg}")


def aviso(arq, msg, linha=None):
    avisos.append(f"{arq}{':' + str(linha) if linha else ''}  {msg}")


def existe_exato(raiz, rel):
    """True so se o arquivo existe com as MESMAS maiusculas/minusculas (o Windows nao diferencia, o GitHub Pages sim)."""
    atual = raiz
    for parte in rel.split("/"):
        if not atual.is_dir() or parte not in {p.name for p in atual.iterdir()}:
            return False
        atual = atual / parte
    return atual.is_file()


def externo(url):
    u = (url or "").strip()
    return u.lower().startswith(("http://", "https://", "//"))


def host_de(url):
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    return (urlparse(u).hostname or "").lower()


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------
class Auditor(HTMLParser):
    def __init__(self, nome):
        super().__init__(convert_charrefs=True)
        self.nome = nome
        self.csp = None
        self._script_inline = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        ln = self.getpos()[0]
        n = self.nome

        # atributos perigosos em qualquer tag
        for k, v in a.items():
            if v.strip().lower().startswith("javascript:"):
                falha(n, f"URL 'javascript:' em <{tag} {k}>", ln)

        if tag == "meta":
            he = a.get("http-equiv", "").lower()
            if he == "content-security-policy":
                self.csp = a.get("content", "")
            if he == "refresh":
                falha(n, "meta refresh (redirecionamento automatico) nao e permitido", ln)
        elif tag == "base":
            falha(n, "<base> nao e permitido (permite sequestrar todos os links)", ln)
        elif tag in ("iframe", "frame", "object", "embed"):
            falha(n, f"<{tag}> nao e permitido", ln)
        elif tag == "script":
            src = a.get("src", "")
            if src:
                if externo(src):
                    if host_de(src) not in PERMITIDOS["hosts_script"]:
                        falha(n, f"script externo nao permitido: {src}", ln)
                    elif not a.get("integrity"):
                        falha(n, f"script externo sem integrity (SRI): {src}", ln)
            else:
                self._script_inline = ln
        elif tag == "link":
            href = a.get("href", "")
            if externo(href):
                falha(n, f"recurso externo em <link>: {href}", ln)
        elif tag == "img":
            src = a.get("src", "")
            if externo(src) or src.lower().startswith("data:"):
                falha(n, f"<img> com origem externa/data: {src[:60]}", ln)
        elif tag == "form":
            if externo(a.get("action", "")):
                falha(n, f"formulario enviando para fora: {a.get('action')}", ln)
        elif tag == "a":
            href = a.get("href", "").strip()
            low = href.lower()
            if low.startswith("mailto:"):
                mail = low[7:].split("?")[0]
                if mail not in PERMITIDOS["emails"]:
                    falha(n, f"e-mail nao permitido: {mail}", ln)
            elif low.startswith("tel:"):
                dig = re.sub(r"\D", "", low)
                if dig not in PERMITIDOS["whatsapp"]:
                    falha(n, f"telefone nao permitido: {href}", ln)
            elif low.startswith("http://"):
                falha(n, f"link sem HTTPS: {href}", ln)
            elif externo(href):
                h = host_de(href)
                if h not in PERMITIDOS["dominios_links"]:
                    falha(n, f"link para dominio nao permitido ({h}): {href[:80]}", ln)
                if h == "wa.me":
                    m = re.match(r"https?://wa\.me/(\d+)", low)
                    if not m or m.group(1) not in PERMITIDOS["whatsapp"]:
                        falha(n, f"numero de WhatsApp nao permitido: {href}", ln)
            if a.get("target", "").lower() == "_blank":
                rel = a.get("rel", "").lower().split()
                if "noopener" not in rel:
                    falha(n, 'link com target="_blank" sem rel="noopener noreferrer"', ln)

    def handle_data(self, data):
        if self._script_inline is not None:
            base = self._script_inline
            for pad, msg in [
                (r"\beval\s*\(", "eval() nao e permitido"),
                (r"\bnew\s+Function\s*\(", "new Function() nao e permitido"),
                (r"document\.write(ln)?\s*\(", "document.write() nao e permitido"),
                (r"\b(window\.|top\.|self\.|document\.)?location(\.href)?\s*=[^=]", "redirecionamento via location nao e permitido"),
                (r"\blocation\.(replace|assign)\s*\(", "redirecionamento via location nao e permitido"),
            ]:
                for m in re.finditer(pad, data):
                    falha(self.nome, msg, base + data[:m.start()].count("\n"))
            for pad, msg in [
                (r"\batob\s*\(", "atob() (comum em codigo ofuscado) - confira"),
                (r"\bunescape\s*\(", "unescape() - confira"),
                (r"String\.fromCharCode", "String.fromCharCode - confira"),
                (r"\b(localStorage|sessionStorage|document\.cookie)\b", "armazenamento no navegador - se guardar dado do cliente, revise LGPD"),
            ]:
                for m in re.finditer(pad, data):
                    aviso(self.nome, msg, base + data[:m.start()].count("\n"))

    def handle_endtag(self, tag):
        if tag == "script":
            self._script_inline = None


def checar_csp(nome, csp):
    if not csp:
        falha(nome, "falta <meta http-equiv=\"Content-Security-Policy\">")
        return
    d = {}
    for parte in csp.split(";"):
        p = parte.strip().split()
        if p:
            d[p[0].lower()] = p[1:]
    for obrigatoria in ("default-src", "script-src", "connect-src", "object-src", "base-uri", "form-action"):
        if obrigatoria not in d and not (obrigatoria == "script-src" and "default-src" in d):
            falha(nome, f"CSP sem a diretiva {obrigatoria}")
    for dire, vals in d.items():
        if "'unsafe-eval'" in vals:
            falha(nome, f"CSP {dire} com 'unsafe-eval'")
        for v in vals:
            if v in ("*", "https:", "http:", "data:") and dire in ("script-src", "connect-src", "default-src", "object-src", "frame-src"):
                if not (dire == "img-src"):
                    falha(nome, f"CSP {dire} muito aberta ({v})")
            if dire in ("script-src", "connect-src", "style-src") and "." in v and not v.startswith("'"):
                h = v.replace("https://", "").split("/")[0]
                permitidos = PERMITIDOS["hosts_connect"] if dire == "connect-src" else PERMITIDOS["hosts_script"]
                if h not in permitidos:
                    falha(nome, f"CSP {dire} libera host nao permitido: {v}")
    if "'unsafe-inline'" in d.get("script-src", []):
        aviso(nome, "CSP script-src ainda tem 'unsafe-inline' (divida conhecida: handlers onclick inline)")


def checar_cotacao(nome, texto, csp):
    """URL_COTACAO: vazia (envio desativado) ou https://<host permitido>/ liberado no connect-src da CSP."""
    m = re.search(r"const\s+URL_COTACAO\s*=\s*'([^']*)'", texto)
    if not m:
        aviso(nome, "constante URL_COTACAO nao encontrada (envio de cotacao)")
        return
    url = m.group(1).strip()
    if not url:
        return
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        falha(nome, f"URL_COTACAO precisa ser https: {url}")
        return
    if u.hostname.lower() not in PERMITIDOS["hosts_connect"]:
        falha(nome, f"URL_COTACAO aponta para host fora de PERMITIDOS['hosts_connect']: {u.hostname}")
    connect = []
    for parte in (csp or "").split(";"):
        p = parte.strip().split()
        if p and p[0].lower() == "connect-src":
            connect = p[1:]
    if f"https://{u.hostname}" not in connect and f"https://{u.hostname}/" not in connect:
        falha(nome, f"a CSP (connect-src) nao libera {u.hostname}: o navegador bloquearia o envio")


def auditar_html(caminho, raiz):
    nome = str(caminho.relative_to(raiz)).replace("\\", "/")
    texto = caminho.read_text(encoding="utf-8", errors="replace")
    p = Auditor(nome)
    p.feed(texto)
    checar_csp(nome, p.csp)
    checar_cotacao(nome, texto, p.csp)
    # qualquer URL escrita no codigo (script, css, atributos)
    for m in re.finditer(r"""https?://([A-Za-z0-9.\-]+)""", texto):
        h = m.group(1).lower()
        if h not in PERMITIDOS["hosts_em_texto"] and h not in PERMITIDOS["hosts_connect"]:
            falha(nome, f"URL para host nao permitido no codigo: {m.group(0)}", texto[:m.start()].count("\n") + 1)
    for m in re.finditer(r"""(?<!\d)(55\d{10,11})(?!\d)""", texto):
        if m.group(1) not in PERMITIDOS["whatsapp"]:
            falha(nome, f"numero de telefone nao permitido: {m.group(1)}", texto[:m.start()].count("\n") + 1)
    for m in re.finditer(r"@import\b", texto):
        falha(nome, "@import em CSS nao e permitido", texto[:m.start()].count("\n") + 1)


# ----------------------------------------------------------------------------
# Popup de cotacao x rele: as listas de opcoes tem que ser iguais
# ----------------------------------------------------------------------------
SELECTS_COTACAO = {
    "cot-flex": "flex", "cot-perfil": "perfil", "cot-orcamento": "orcamento", "cot-hotel": "hotelCategoria",
    "cot-regime": "hotelRegime", "cot-bagagem": "bagagem", "cot-voo": "preferenciaVoo", "cot-passaporte": "passaporte",
    "cot-canal": "canal", "cot-horario": "horario", "cot-conheceu": "comoConheceu",
}


def auditar_cotacao_sync(raiz):
    html_p, worker_p = raiz / "index.html", raiz / "cotacao-worker" / "worker.js"
    if not (html_p.exists() and worker_p.exists()):
        return
    html = html_p.read_text(encoding="utf-8", errors="replace")
    worker = worker_p.read_text(encoding="utf-8", errors="replace")
    bloco = re.search(r"const OPCOES = \{(.*?)\n\};", worker, re.S)
    if not bloco:
        falha("cotacao-worker/worker.js", "nao achei a lista OPCOES para comparar com o popup")
        return
    opcoes = {}
    for m in re.finditer(r"(\w+):\s*\[(.*?)\]", bloco.group(1), re.S):
        opcoes[m.group(1)] = {a or b for a, b in re.findall(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"", m.group(2))} - {""}
    def confere(campo, do_html):
        if opcoes.get(campo) != do_html:
            so_html, so_worker = sorted(do_html - opcoes.get(campo, set())), sorted(opcoes.get(campo, set()) - do_html)
            falha("index.html x cotacao-worker/worker.js", f"opcoes de '{campo}' diferentes. So no popup: {so_html}. So no rele: {so_worker}")
    for sel_id, campo in SELECTS_COTACAO.items():
        m = re.search(rf'<select id="{sel_id}">(.*?)</select>', html, re.S)
        if not m:
            falha("index.html", f"popup de cotacao sem o campo {sel_id}")
            continue
        confere(campo, set(re.findall(r'<option value="([^"]*)"', m.group(1))) - {""})
    confere("servicos", set(re.findall(r'name="cot-servico" value="([^"]+)"', html)))


# ----------------------------------------------------------------------------
# JSON de dados
# ----------------------------------------------------------------------------
RE_TEXTO_SEGURO = re.compile(r"^[^<>\"`\\\x00-\x1f]{1,80}$")
RE_CAMINHO = re.compile(r"^(images|banners)/[a-z0-9_\-/]+\.(jpe?g|png|webp)$", re.I)
RE_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def auditar_json(raiz):
    fo = raiz / "ofertas.json"
    if fo.exists():
        try:
            dados = json.loads(fo.read_text(encoding="utf-8-sig"))
            assert isinstance(dados, list)
            for i, o in enumerate(dados):
                if not isinstance(o, dict):
                    falha("ofertas.json", f"item {i} nao e objeto"); continue
                if not RE_TEXTO_SEGURO.match(str(o.get("destino", ""))):
                    falha("ofertas.json", f"item {i}: 'destino' vazio ou com caracteres proibidos (< > \" ` \\)")
                f = str(o.get("feed", ""))
                if not RE_CAMINHO.match(f) or ".." in f:
                    falha("ofertas.json", f"item {i}: 'feed' fora do padrao images/... ou banners/...: {f[:60]}")
                elif not existe_exato(raiz, f):
                    falha("ofertas.json", f"item {i}: arquivo nao existe no repo (confira maiusculas/minusculas): {f}")
                if "stories" in o:
                    st = str(o.get("stories", ""))
                    if not RE_CAMINHO.match(st) or ".." in st:
                        falha("ofertas.json", f"item {i}: 'stories' fora do padrao images/... ou banners/...: {st[:60]}")
                    elif not existe_exato(raiz, st):
                        falha("ofertas.json", f"item {i}: arquivo nao existe no repo (confira maiusculas/minusculas): {st}")
                if "validade" in o and not RE_DATA.match(str(o.get("validade", ""))):
                    falha("ofertas.json", f"item {i}: 'validade' (opcional) fora do formato AAAA-MM-DD")
        except (ValueError, AssertionError) as e:
            falha("ofertas.json", f"JSON invalido ou nao e uma lista ({e})")
    fd = raiz / "destinos.json"
    if fd.exists():
        try:
            dados = json.loads(fd.read_text(encoding="utf-8-sig"))
            assert isinstance(dados, dict)
            for slug, d in dados.items():
                if not RE_SLUG.match(slug):
                    falha("destinos.json", f"slug fora do padrao: {slug[:40]}")
                if not isinstance(d, dict) or not RE_TEXTO_SEGURO.match(str(d.get("nome", ""))):
                    falha("destinos.json", f"{slug}: 'nome' vazio ou com caracteres proibidos"); continue
                for g in d.get("galeria", []) or []:
                    if not RE_CAMINHO.match(str(g)) or ".." in str(g):
                        falha("destinos.json", f"{slug}: caminho de galeria fora do padrao: {str(g)[:60]}")
                    elif not existe_exato(raiz, str(g)):
                        falha("destinos.json", f"{slug}: arquivo de galeria nao existe (confira maiusculas/minusculas): {g}")
        except (ValueError, AssertionError) as e:
            falha("destinos.json", f"JSON invalido ou nao e um objeto ({e})")


# ----------------------------------------------------------------------------
# Arquivos, segredos, workflows
# ----------------------------------------------------------------------------
def auditar_arquivos(raiz, eu):
    for p in sorted(raiz.rglob("*")):
        if any(x in IGNORAR_PASTAS for x in p.relative_to(raiz).parts) or not p.is_file():
            continue
        rel = str(p.relative_to(raiz)).replace("\\", "/")
        if p.name.lower() in IGNORAR_NOMES:
            continue
        ext = p.suffix.lower()
        if ext in EXT_PROIBIDAS:
            falha(rel, "tipo de arquivo executavel/proibido")
        if rel.startswith(("images/", "banners/")) and ext not in EXT_IMAGEM:
            falha(rel, "em images/ e banners/ so entram .jpg .jpeg .png .webp (SVG/HTML/JS podem carregar codigo)")
        if p.name.lower() in (".env", "id_rsa", "id_ed25519") or ext in (".pem", ".key", ".pfx", ".p12"):
            falha(rel, "arquivo de credencial no repositorio")
        if ext in EXT_TEXTO and p.resolve() != eu and p.stat().st_size < 5_000_000:
            txt = p.read_text(encoding="utf-8", errors="replace")
            for nome, rx in SEGREDOS:
                for m in rx.finditer(txt):
                    falha(rel, f"possivel segredo ({nome})", txt[:m.start()].count("\n") + 1)
        if rel.startswith(".github/workflows/") and ext in (".yml", ".yaml"):
            txt = p.read_text(encoding="utf-8", errors="replace")
            if "pull_request_target" in txt:
                falha(rel, "pull_request_target e perigoso; use pull_request")
            if re.search(r"permissions:\s*write-all", txt):
                falha(rel, "permissions: write-all nao e permitido")
            for m in re.finditer(r"uses:\s*([^\s#]+)", txt):
                u = m.group(1)
                if not u.startswith("./") and not re.search(r"@[0-9a-f]{40}$", u):
                    falha(rel, f"action sem fixar em SHA de commit: {u}")


def main():
    raiz = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    eu = Path(__file__).resolve()
    print(f"Verificando: {raiz}")
    for h in sorted(list(raiz.glob("*.html")) + list(raiz.glob("*.htm"))):
        auditar_html(h, raiz)
    auditar_json(raiz)
    auditar_cotacao_sync(raiz)
    auditar_arquivos(raiz, eu)

    for a in avisos:
        print("AVISO  " + a)
    for f in falhas:
        print("FALHA  " + f)
    if falhas:
        print(f"\nRESULTADO: {len(falhas)} FALHA(S), {len(avisos)} aviso(s). NAO PUBLIQUE ate corrigir.")
        sys.exit(1)
    print(f"\nRESULTADO: OK ({len(avisos)} aviso(s)).")


if __name__ == "__main__":
    main()
