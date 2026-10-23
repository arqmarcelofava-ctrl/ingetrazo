# Recomeçar o projeto numa conta nova

*(em linguagem simples; escrito em 2026-10-23, ao trocar de conta do Claude)*

Tudo o que foi feito está guardado em dois lugares:

1. **No GitHub**, no repositório `arqmarcelofava-ctrl/ingetrazo`, na branch
   **`spike/viabilidade-bim`**: código, testes, documentos, imagens e a
   apresentação. Nada se perde quando a conta antiga fecha.
2. **Num pacote privado** (`dados-privados-apto.zip`, entregue no chat da conta
   antiga), que **não pode ir para o GitHub**: o DWG e o PDF do apartamento real,
   o DXF convertido, os modelos `.igz` montados com ele, as imagens dele e os
   scripts de trabalho. Guarde esse arquivo no seu computador ou no seu Drive.

## Caminho A — conta nova do Claude, mesmo GitHub (o mais simples)

1. Entre na conta nova em claude.ai/code.
2. Conecte o GitHub (o mesmo usuário `arqmarcelofava-ctrl`).
3. Crie uma sessão com o repositório **`arqmarcelofava-ctrl/ingetrazo`**.
4. Cole esta mensagem:

> Leia `CLAUDE.md` e `docs/CONTINUAR.md`. Trabalhe na branch
> `spike/viabilidade-bim`. Responda em português simples e me mostre imagens.
> Vou te mandar o DWG e o PDF do apartamento: eles nunca podem entrar no Git.

5. Se for usar o apartamento real, envie no chat o DWG e o PDF que estão dentro
   do `dados-privados-apto.zip` (pasta `modelos/originais/`).

## Caminho B — GitHub diferente

1. Baixe o `ingetrazo-spike-bim.zip` (entregue no chat da conta antiga). Ele é o
   projeto inteiro, igual à branch.
2. Crie um repositório novo no GitHub da conta nova e suba o conteúdo do zip
   (ou peça ajuda ao Claude da conta nova para isso), na branch
   `spike/viabilidade-bim`.
3. Siga o Caminho A a partir do passo 3, com o repositório novo.

## Caminho C — no seu computador

Sua pasta: `C:\Users\Marcelo\Documents\CLAUDE_AO\Ingetrazo Bim`.

1. Clique com o botão direito em `ingetrazo-spike-bim.zip` › **Extrair tudo** ›
   escolha a pasta acima. O projeto fica direto dentro dela.
2. Faça o mesmo com `dados-privados-apto.zip`, **na mesma pasta**. Ele já cria
   `modelos\` (que o Git ignora de propósito) com o apartamento dentro.
3. Para abrir o programa, siga `docs/LOCAL.md` a partir do passo 1 (instalar
   Python e Git) e pule o passo 2, porque o projeto já está baixado.

O zip não traz o histórico do Git. Para continuar salvando no GitHub, prefira
baixar com `git clone` (passo 2 do `docs/LOCAL.md`) numa pasta ao lado e copie
para ela a pasta `modelos\`. **No Windows importe o DXF** (`modelos/.../apto.dxf`), porque o
conversor de DWG que vem no projeto só funciona no Linux.

## O que está nesta pasta

| Arquivo | Para quê |
|---|---|
| `LEIA-PRIMEIRO.md` | Este guia. |
| `apresentacao/` | Os 19 slides da apresentação («De onde partimos até onde chegamos») e como recriá-la na conta nova. |

E nos outros lugares do repositório:

| Arquivo | Para quê |
|---|---|
| `CLAUDE.md` | Regras curtas que o Claude lê sozinho ao abrir o projeto. |
| `docs/CONTINUAR.md` | **Passagem de bastão completa**: quem é você, decisões, o que foi aprendido, armadilhas e o que fazer a seguir. |
| `docs/RESUMO.md` | O que existe hoje e como entra no IngeTrazo, em etapas. |
| `docs/spike-viabilidade.md` | O relatório técnico: as 6 perguntas, achados 1 a 20 e mudanças no núcleo. |
| `docs/LOCAL.md` | Como rodar no computador (Windows). |
| `plugins/bim_proto.py` | A extensão (todo o BIM leve). |
| `tests/test_bim_proto.py`, `tests/test_spike_viabilidade.py` | 196 testes automáticos. |
| `docs/images/` | Imagens sintéticas e os scripts que as geram. |

## O pacote privado (`dados-privados-apto.zip`)

| Pasta | Conteúdo |
|---|---|
| `modelos/originais/` | O DWG e o PDF de entrega que você mandou. |
| `modelos/apto/` | Cópia do DWG (`in/apto.dwg`), o DXF convertido (`out/apto.dxf`), paredes e vãos extraídos (`walls.json`, `vaos.json`), modelos montados (`*.igz`), biblioteca de portas de teste (`biblioteca_portas.skp`) e imagens do apartamento (`img/`). |
| `modelos/scripts/` | Scripts de trabalho usados com o apartamento (por exemplo `modelo_completo.py`, `modelo_ambientes.py`, `import_real.py`, `swing_check.py`, `draw_interv.py`). Eles esperam os arquivos em `apto/` (em `modelos/apto/`); ajuste os caminhos ao usar. |

Lembrete: as coordenadas do modelo são as do DWG menos (2,2972; −0,0131).
