# Levar o projeto para o computador (passo a passo)

*(feito para quem não é de informática; uma pessoa na frente do computador faz os passos 1 a 4)*

**O que muda:** na nuvem não há tela, então o 3D nunca apareceu e o mouse nunca foi testado. No
computador, o IngeTrazo abre de verdade, com o painel **«Projeto»** e os botões. Tudo o que foi
feito está na ramificação `spike/viabilidade-bim` do GitHub; nada se perde.

## 1. Instalar duas coisas (uma vez só)

- **Python 3.12 ou 3.13** — python.org/downloads. Na primeira tela, marque **«Add python.exe to PATH»**.
- **Git** — git-scm.com/download/win (aceite as opções padrão). O Git é necessário porque uma das
  peças do programa (leitor de `.skp`) é baixada pelo GitHub.

## 2. Baixar o projeto

Abra o **Prompt de Comando** (tecla Windows, digite `cmd`) e rode, uma linha por vez:

```
cd %USERPROFILE%\Documents\M.ARTE_NOVO
git clone -b spike/viabilidade-bim https://github.com/arqmarcelofava-ctrl/ingetrazo INGETRAZO
cd INGETRAZO
```

(Se o GitHub pedir login, entre com a conta dona do projeto. Se a pasta `INGETRAZO` já existir com
arquivos seus, escolha outro nome no lugar de `INGETRAZO`.)

## 3. Preparar o programa (uma vez só)

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Abrir

```
python main.py
```

O IngeTrazo abre. Na aba lateral aparece **«Projeto»** (ao lado de Propriedades e Navegador), com os
botões: Parede (com menu: reta, curva, contínua, retângulo), Importar DWG, Unir cantos, Portas,
Peitoris, Detectar ambientes, Áreas, Planta 2D e Atualizar 3D.
Da próxima vez, só abra o Prompt, `cd` até a pasta, `venv\Scripts\activate` e `python main.py`.

## 5. Atenção: DWG no Windows

O conversor de DWG que vem no projeto (`vendor/libredwg/bin/dwg2dxf`) é **só para Linux**. No Windows,
o botão **Importar DWG** não consegue ler `.dwg`. Duas saídas:

1. **Salvar o desenho como DXF** (no AutoCAD: *Salvar como… DXF*; ou num conversor gratuito) e importar o
   `.dxf` — o botão aceita os dois.
2. Colocar um `dwg2dxf.exe` do LibreDWG para Windows na pasta `vendor\libredwg\bin`.

## 6. Arquivos grandes (o `.glb` de 300 MB, o `.skp`)

Copie para a pasta `modelos` dentro do projeto (ela é ignorada pelo Git de propósito: arquivo de
cliente **nunca** vai para o GitHub). Para olhar um modelo sem abrir o programa:

```
python scripts\inspecionar_modelo.py modelos\NOME.glb --so-relatorio
```

## 7. Continuar com o Claude Code no computador

No Prompt, dentro da pasta do projeto (com o `venv` ativo ou não):

```
claude
```

e escreva: **«Leia docs/CONTINUAR.md e continue.»** O arquivo `CLAUDE.md` já manda o Claude ler o
histórico, falar em português simples e mostrar imagens. Para aprovar as permissões **pelo celular**,
rode `claude remote-control` (atalho `claude rc`) em vez de `claude`; a sessão aparece no app do
Claude Code.

## 8. Guardar o trabalho de volta

```
git add -A
git commit -m "descreva o que mudou"
git push
```

Nunca use `git add` em arquivos de cliente (a pasta `modelos` já é ignorada). Se também for continuar
na nuvem depois, faça sempre `git pull` antes de começar.

## O que testar primeiro (nada disso foi visto com tela e mouse reais)

1. Abrir o programa e ver se o painel **«Projeto»** aparece, com os ícones.
2. **Parede ▾ → Retângulo**, clicar dois cantos: devem sair 4 paredes; **Ctrl+Z** desfaz as 4 de uma vez.
3. **Parede ▾ → Curva**: início, fim e um ponto do arco (o arco aparece no elástico).
4. **Importar DWG/DXF** com a planta do apartamento; depois **Detectar ambientes** e **Áreas**.
5. Ver o 3D sombreado das paredes, portas e esquadrias (a parte que a nuvem nunca mostrou) e anotar
   qualquer coisa estranha (parede transparente, sombra errada, clique que não pega).
