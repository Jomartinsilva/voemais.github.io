#!/usr/bin/env python3
"""
Confere se o site PUBLICADO (GitHub Pages) esta igual ao repositorio local.

Uso:  python scripts/conferir_publicacao.py [URL_BASE] [--espera SEGUNDOS]
Padrao: https://jomartinsilva.github.io/voemais.github.io/   e espera de ate 600 s.

O que faz:
  1. Espera o index.html publicado ficar IDENTICO ao local (o Pages leva alguns minutos).
  2. Confere que index.html, destinos.json e ofertas.json publicados sao identicos aos locais.
  3. Confere que toda imagem citada (logo, capas, galerias, banners) existe no ar (200).
     Diferenca de maiuscula/minuscula no nome aparece aqui: o Windows nao diferencia, o Pages sim.
  4. Confere que arquivos de desenvolvimento NAO foram publicados (CLAUDE.md, scripts/, _config.yml).
Saida: 0 = tudo certo | 1 = algo errado (leia as linhas FALHA).
"""
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_PADRAO = "https://jomartinsilva.github.io/voemais.github.io/"
NAO_PUBLICAR = ["CLAUDE.md", "_config.yml", "scripts/verificar_seguranca.py", "scripts/conferir_publicacao.py", "cotacao-worker/worker.js"]
RAIZ = Path(__file__).resolve().parent.parent


def baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "conferir-publicacao/1.0", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # rede, DNS, timeout
        return 0, str(e).encode()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    espera = 600
    if "--espera" in sys.argv:
        espera = int(sys.argv[sys.argv.index("--espera") + 1])
        args = [a for a in args if a != str(espera)]
    base = (args[0] if args else BASE_PADRAO).rstrip("/") + "/"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    local_html = (RAIZ / "index.html").read_bytes()
    print(f"Site: {base}")
    print(f"Esperando o index.html publicado ficar igual ao local (ate {espera}s)...")
    limite = time.time() + espera
    ok_html = False
    while True:
        st, corpo = baixar(base)
        if st == 200 and sha(corpo) == sha(local_html):
            ok_html = True
            break
        if time.time() >= limite:
            print(f"FALHA  index.html publicado NAO ficou igual ao local (status {st}). "
                  "Veja a aba Actions do GitHub ('pages build and deployment').")
            sys.exit(1)
        time.sleep(20)
    print("OK     index.html publicado identico ao local")

    falhas, avisos = [], []

    # JSONs identicos
    for nome in ("destinos.json", "ofertas.json"):
        p = RAIZ / nome
        if not p.exists():
            avisos.append(f"{nome} nao existe no repositorio")
            continue
        st, corpo = baixar(base + nome)
        if st != 200:
            falhas.append(f"{nome} nao esta no ar (status {st})")
        elif sha(corpo) != sha(p.read_bytes()):
            falhas.append(f"{nome} publicado difere do local (espere o deploy ou confira o commit)")

    # imagens citadas
    caminhos = set()
    html_txt = local_html.decode("utf-8", errors="replace")
    for m in re.finditer(r"""(?:images|banners)/[A-Za-z0-9_\-/.]+\.(?:jpe?g|png|webp)""", html_txt, re.I):
        caminhos.add(m.group(0))
    for nome in ("destinos.json", "ofertas.json"):
        p = RAIZ / nome
        if p.exists():
            for m in re.finditer(r""""((?:images|banners)/[^"]+)\"""", p.read_text(encoding="utf-8-sig")):
                caminhos.add(m.group(1))
    sem_foto = []
    for c in sorted(caminhos):
        if not (RAIZ / c).is_file():
            # citada no HTML mas sem arquivo local: o site usa o plano B (ex.: capa do Orlando)
            sem_foto.append(c)
            continue
        st, corpo = baixar(base + c)
        if st != 200:
            falhas.append(f"imagem no repo mas NAO no ar (status {st}): {c}  (confira maiusculas/minusculas)")
        elif len(corpo) != (RAIZ / c).stat().st_size:
            falhas.append(f"imagem no ar com tamanho diferente do local: {c}")
    if sem_foto:
        avisos.append("citadas no codigo, sem arquivo (o site usa o plano B): " + ", ".join(sem_foto))

    # dev nao publicado
    for c in NAO_PUBLICAR:
        st, _ = baixar(base + c)
        if st == 200:
            falhas.append(f"arquivo de desenvolvimento esta PUBLICADO: {c} (confira o _config.yml)")

    for a in avisos:
        print("AVISO  " + a)
    for f in falhas:
        print("FALHA  " + f)
    if falhas:
        print(f"\nRESULTADO: {len(falhas)} FALHA(S).")
        sys.exit(1)
    print(f"\nRESULTADO: site publicado confere com o repositorio ({len(caminhos) - len(sem_foto)} imagens no ar).")


if __name__ == "__main__":
    main()
