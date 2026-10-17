# Continuar o spike de BIM leve (leia isto primeiro)

Este arquivo é a **passagem de bastão**: tudo que um Claude novo precisa para
continuar sem a conversa original. Foi escrito em 2026-10-17, na branch
`spike/viabilidade-bim`. **Responda sempre em português do Brasil.**

## 1. Quem é o usuário e como falar com ele

- É arquiteto(a), **não é de informática**, e usa o Claude **pelo celular**.
  Explique em linguagem simples, curta, sem jargão. Quando um passo for
  técnico, diga o que ele faz em uma frase. Passos longos de terminal já foram
  recusados ("ficou infinitamente mais complexo"): prefira o caminho com menos
  passos e peça uma coisa de cada vez.
- **"Sempre que possível quero visualizar."** Sempre que der, gere imagem
  (planta, axonometria, captura da janela) em vez de só descrever, e mostre.
- As aprovações de permissão devem poder ser feitas **pelo celular**
  (`/remote-control` na sessão local).
- Se algo travar, pare e explique; nada de gambiarra nem de resultado inventado.

## 2. O objetivo

Construir, **só com extensões** (`plugins/` e `docs/`, sem tocar em `core/` nem
`tools/`), um **BIM leve para a fase de Conceito** (planta + 3D): navegador de
projeto, pavimentos, vistas, desenho 2D paramétrico (parede, vão, zona de piso,
ambientes), cenários e pranchas. IngeTrazo é um fork GPL-3.0, Python 3.12+.

Regras do usuário: usar só símbolos públicos e marcar com `FRAGILE` qualquer
interno; conferir a licença de qualquer dependência nova (compatível com
GPL-3.0); **nenhum dado real de cliente no repositório**.

## 3. O que já está feito (tudo nesta branch)

| O quê | Onde |
|---|---|
| Resposta às 6 perguntas de viabilidade, tabela, o que exigiria mudar o núcleo, o que ficou sem verificar | `docs/spike-viabilidade.md` |
| Protótipo: ferramenta de parede por dois pontos, parâmetros em `group.ext`, níveis em `plugin_data`, **Atualizar 3D** num passo de desfazer, painel «Project» (árvore), importar planta (`wall_from_rectangle`), criação em lote (`create_walls`), vãos de porta (`wall_gaps`), **aberturas de verdade nas paredes** (`openings`, `merge_gap_walls`), **união de cantos** em L/T (`join_walls`, `join_corners`) **esquadrias** de fachada e guarda-corpo com travessas (`kind: "frame"`, `create_frames`), **peitoris** (`create_sills`) e **folhas de porta** (`create_leaves`) | `plugins/bim_proto.py` |
| Testes (121, todos passam) | `tests/test_bim_proto.py`, `tests/test_spike_viabilidade.py` |
| Inspeciona qualquer modelo (.igz .glb .skp .dae .obj .stl .dxf .dwg) e desenha planta cortada, frente e axonometria **sem placa de vídeo** | `scripts/inspecionar_modelo.py` |
| Imagens de exemplo (paredes de teste, janela com o painel) | `docs/images/` |

Resumo das respostas: painel com árvore **SIM**; dados no arquivo salvo **SIM**
(`plugin_data` + `group.ext`); vistas/cortes por código **SIM** (com ressalvas);
ferramentas de desenho **PARCIAL** (a persistente usa `viewport.set_active_tool`,
fora do contrato); regenerar o 3D num passo de desfazer **SIM**; prancha/PDF/SVG
**PARCIAL** (PDF só por caminho privado, SVG não existe).

## 4. O que foi aprendido com uma planta real (DWG de um projeto em andamento)

O usuário forneceu um DWG de planta de apartamento. **Ele e as imagens dele
não estão no repositório e nunca devem entrar.** Achados que valem para
qualquer planta importada:

1. O importador do núcleo abre DWG (conversor `vendor/libredwg/bin/dwg2dxf`),
   mas **ignora as hachuras** (`HATCH`), que é como a alvenaria costuma ser
   desenhada. As paredes chegam só como contorno.
2. Alvenaria vem como **blocos anônimos** com polígono + hachura; a estrutura
   pode estar em **blocos aninhados**. Explodir recursivamente.
3. **Blocos espelhados** (escala −1) trazem o polígono com extrusão `(0,0,−1)`,
   em coordenadas do objeto (OCS). Use `LWPolyline.vertices_in_wcs()`; ler os
   pontos crus espelha a parede em X. (Foi um erro real.)
4. O espaço-modelo pode ter **duas plantas empilhadas** (base e proposta);
   escolha qual reconstruir.
5. O DWG **não tem alturas nem níveis** (Z ≈ 0). O usuário definiu, em
   2026-10-17: **pé-direito 2,80 m "por enquanto"** (provisório) e **portas de
   2,10 m** (valem para todas, inclusive as de shaft que o nome do bloco dá
   como 1,96 m). Use esses valores; só mude se o usuário disser.
6. Vãos de porta aparecem como **lacunas** entre trechos de parede alinhados.
   `wall_gaps` as acha e `merge_gap_walls` funde os trechos numa parede contínua
   com a abertura (`openings`); sem fundir, a verga é `create_walls(...,
   base_offset=altura_da_porta)`. Ligou 12 de 18 portas, e a largura do vão bate
   com a da porta (80 cm → 87 cm). Uma peça curta entre dois vãos fazia a mesma
   porta aparecer duas vezes: as aberturas sobrepostas são unidas.
   O nome do bloco da porta (Revit) traz largura e altura ("80 x 210 cm").
7. Paredes desenhadas por eixo **não se uniam nos cantos**. Agora `join_corners`
   resolve L e T. **Não use em planta importada** (as peças do DWG já fecham; com
   tolerância 0,30 m a união mexeria em 77 de 134 encontros). Base × proposta
   (planta real): 27,13 m² de parede mantida, 1,62 m² a demolir, 3,80 m² nova.
8. Esquadrias da fachada: 4 sistemas (11 montantes) + 3 guarda-corpos reconstruídos
   do DWG; **alturas provisórias suposição do assistente (2,80 m e 1,10 m)**,
   o usuário ainda não confirmou. Travessas (pé, 1,00 m, topo; 5 cm) também supostas.
   Folhas de porta: 8, fechadas (o sentido de abertura não está no DWG). Peitoris: o DWG
   não tem janela em parede, então só foram testados com exemplo sintético.

Armadilhas de **desenho** (custaram tempo):
- O backend Qt do `ezdxf` desenha com **Y para baixo**: a imagem sai de cabeça
  para baixo. Espelhe verticalmente (`QImage.mirrored(False, True)`).
- Para o passe `core.hlr`, `OrbitCamera` com `yaw=-90°` e `pitch=89.9°` dá uma
  planta com **norte para cima, sem espelhar** (`u=x, v=y`).
- `Group` de planta importada e `create_wall` por parede custam **um passo de
  desfazer por parede**; use `create_walls` (lote, um passo, tudo ou nada).
- Confirme resultados **numericamente**, não só a olho (foi assim que se
  provou que a reconstrução bate com o DWG a menos de 5 mm).

## 5. O que o usuário quer agora

O usuário tem um modelo 3D do **mesmo apartamento** (o living, versão de
render), que é um `.glb` de ~300 MB com texturas (e o `.skp` original de
~294 MB). **Não cabe no chat**; por isso a continuação é **local**, no computador
onde o arquivo está. O que o `.glb` acrescenta ao DWG: mobília, acabamentos e o
3D real do living. As paredes e portas já vêm do DWG.

**Faça, nesta ordem, e mostre imagens a cada etapa:**

1. Descubra a pasta do projeto (onde este arquivo está) e se o `.glb` está em
   `modelos/` (se estiver em outro lugar, **copie** para `modelos/`; essa pasta
   está no `.gitignore`). Nunca faça commit do que estiver em `modelos/`.
2. Instale as dependências: `pip install -r requirements.txt` (nenhuma nova).
   Python 3.12 ou 3.13.
3. Rode só o relatório:
   `python scripts/inspecionar_modelo.py "modelos/<arquivo>.glb" --so-relatorio`
   e conte ao usuário, em linguagem simples: quantas peças, nomes dos maiores
   grupos, tamanho em metros e se o script avisou que a unidade não parece metro.
4. Se o modelo tiver mais de 60 mil faces, o desenho pede `--forcar` e pode
   demorar muito: **pergunte antes** de forçar. Alternativa: desenhar só uma
   parte. As imagens saem em `modelos/saida/`; **mostre-as**.
5. Compare com o que o DWG já deu (paredes e portas) e proponha, com honestidade,
   o que o `.glb` acrescenta e o que ele **não** traz (um `.glb` não tem camadas
   BIM nem níveis).
6. Use o pé-direito de 2,80 m e as portas de 2,10 m (decisão do usuário, provisória); se ele trouxer a medida real, troque.
7. Próximos passos de produto, se o usuário quiser: confirmar as alturas das
   esquadrias, travessas e guarda-corpo; sentido de giro das portas; soleira; tornar o
   painel «Project» interativo (clique aplica vista / seleciona parede); levar
   base × proposta para dentro do repositório (função sem `shapely`).

## 6. Como rodar

```
pip install -r requirements.txt
python -m pytest tests/test_bim_proto.py tests/test_spike_viabilidade.py -q
python scripts/inspecionar_modelo.py <modelo> [--so-relatorio] [--corte 1.2] [--forcar]
```

No Linux sem tela use `QT_QPA_PLATFORM=offscreen` (no Windows não precisa).
O ambiente de nuvem onde isto foi feito precisou de `libegl1` para o Qt abrir.

## 7. Ainda sem verificar (falta de interface gráfica)

Desenho sombreado da parede no viewport 3D (OpenGL); interação real com mouse
e o campo de medidas; clique no painel «Project» (hoje só leitura); render de
quadros de vista no compositor; testes com `.skp`, `.dae`, `.obj`, `.stl` e com
um modelo de centenas de MB. Detalhes em `docs/spike-viabilidade.md`, seção 5.

## 8. Mensagem para colar na primeira vez

> Leia docs/CONTINUAR.md e siga a seção 5. O arquivo .glb está na pasta
> modelos. Explique tudo de forma simples e me mostre imagens.
