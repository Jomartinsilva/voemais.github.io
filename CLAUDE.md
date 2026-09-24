# Site Voe Mais — LEIA ANTES DE QUALQUER ALTERAÇÃO

**OBRIGATÓRIO:** antes de mexer em qualquer arquivo deste repositório, leia por inteiro o prompt mestre (arquivo único, contém todas as regras):

    <RAIZ_SITE>/01_CONTEXTO/PROMPT_CLAUDE_CODE.md

`RAIZ_SITE` é a pasta `07_SITE_VOEMAIS` no Google Drive do Josimar (pasta raiz: `01_CONTEXTO`, `02_ARQUIVOS`, `03_SITE_PUBLICADO`, `04_BENCHMARKING`):

    RAIZ_SITE = https://drive.google.com/drive/folders/1aeG6UUhAAgyZh-yet5kAshBclC8zN59G
    (sem sincronização local neste computador — acesso via conector do Google Drive na sessão do Claude Code)

Se não conseguir ler o arquivo: **pare e avise o Josimar**. Não altere nada sem ler.

Regras que valem sempre, mesmo que o arquivo não abra:

- **Regra 1:** toda alteração precisa ser refletida em `RAIZ_SITE` (`03_SITE_PUBLICADO` + `CHANGELOG.md`).
- Antes de todo commit rode `python scripts/verificar_seguranca.py`. Se falhar, não faça commit.
- Nunca coloque senha, token ou chave em arquivo. Nunca afrouxe a CSP do `index.html`, o `scripts/verificar_seguranca.py` ou a lista `PERMITIDOS` sem autorização expressa do Josimar.
