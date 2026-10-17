# Resumo: o que temos hoje e como entra no IngeTrazo

*(escrito em linguagem simples; detalhes técnicos em `docs/spike-viabilidade.md`)*

## O que já existe (tudo numa extensão, sem mexer no núcleo)

Um arquivo, `plugins/bim_proto.py`, mais testes (163 passando), imagens e documentos.

| Peça | O que faz |
|---|---|
| Parede | Desenhada por dois pontos, com espessura, altura e pavimento; os dados ficam no arquivo salvo e o 3D se refaz com **Atualizar 3D** (um único desfazer). |
| Vãos | Portas e janelas são buracos de verdade na parede (porta = recorte até o chão, janela = furo). |
| Cantos | **Unir cantos** resolve encontros em L e em T. |
| Esquadrias | Montantes, painéis e travessas da fachada; guarda-corpo. |
| Peitoril e portas | Peitoril nas janelas; porta detalhada (marco com rebaixo, guarnição, folha de 35 mm, arco de giro) e porta de correr de vidro. |
| Blocos do SketchUp | Lê uma biblioteca `.skp` e põe o bloco certo em cada abertura (cada porta é uma cópia do mesmo componente). |
| Regra de giro | «A porta abre para a parede»: propõe lado e dobradiça e mostra as alternativas. |
| Ambientes | Detecta os cômodos pelas paredes, cria a zona de piso (IfcSpace) e calcula a área útil. |
| Planta 2D | Corte a 1,20 m com **projeções tracejadas** (o que está acima do corte), no padrão gráfico do escritório (poché preto, linha cinza fina, tracejado 9,8/4,3 pt). |
| Comparação | Base × proposta (o que fica, o que sai, o que entra). |
| Importar DWG | Botão no painel: paredes, pilares e vãos de porta do DWG/DXF (blocos aninhados e espelhados, hachuras, plantas empilhadas); no desenho real, 124 paredes, 8 vãos e 3 vergas, conferido contra o desenho a menos de 5 mm. Esquadrias e portas detalhadas entram pelos outros botões/funções. |

## Como aparece na tela

Um painel novo, **«Projeto»**, na aba lateral (junto de Propriedades e Navegador), com **botões de ícone**
agrupados: Desenhar (Parede), Aberturas (Unir cantos, Portas, Peitoris), Ambientes (Detectar, Áreas),
Planta 2D (altura do corte, estilo, nomes dos ambientes) e Elementos (Atualizar 3D); embaixo, a árvore do
projeto (pavimentos, paredes, portas, ambientes com área, vistas, pranchas). Há também o menu
*Extensões ▸ BIM proto*. Os ícones são desenhados no mesmo estilo dos do programa. **Importar DWG está ligado** (escolhe o arquivo, as camadas de parede, a planta e as alturas; um
passo de desfazer). Só a Biblioteca de blocos aparece apagada («Em breve»). Imagem: `docs/images/bim_proto_janela_painel.png`.
O núcleo não deixa a extensão criar barra de ferramentas própria (só menu, painel e menu de contexto).

## O que ainda NÃO está pronto

- **Nada disso foi visto na janela real do programa** (não há tela aqui): sombreado 3D, mouse, menus.
  Tudo foi provado por testes e por imagens geradas sem placa de vídeo.
- **Não há botões** para a maioria das funções (hoje são funções em Python). Só existem o menu «BIM proto»
  e o painel «Project» (que ainda só lista).
- **A planta com projeções não vai para a prancha/PDF**: o núcleo não tem o gancho de pintura necessário.
- Linhas por etiqueta (tipo, espessura, cor) como no SketchUp 2026 e blocos dinâmicos (atributos): não existem.
- No apartamento real só 5 ambientes saíram, porque o modelo reconstruído só tem a camada de alvenaria.
- Alturas das esquadrias, do guarda-corpo e das travessas são **fictícias** por decisão sua.

## Como entra no IngeTrazo (em etapas)

1. **Agora (feito): protótipo na branch `spike/viabilidade-bim`.** Só extensão; nenhuma linha do núcleo mudou.
2. **Etapa 1 — virar produto de extensão, sem mexer no núcleo.** Mover para `examples/extensions/` (o
   empacotamento hoje inclui `plugins/*.py` em toda instalação); criar os **menus e janelinhas** (importar
   planta DWG, unir cantos, detectar ambientes, aplicar regra de giro, carregar biblioteca de blocos, gerar
   planta PNG); painel «Project» clicável; testar numa máquina com tela (Windows, macOS, Linux, Python 3.12).
3. **Etapa 2 — três mudanças pequenas no núcleo** (já listadas e justificadas no documento do spike):
   1. um **gancho de pintura 2D por quadro de prancha**, para a planta com projeções e os ambientes saírem
      impressos (PDF/SVG);
   2. **aviso quando um grupo com dados de extensão é movido/girado/escalado**, para o 3D não ficar
      desatualizado;
   3. **importar as linhas soltas dos componentes `.skp`**, para os símbolos 2D (arco de giro) dos seus
      blocos chegarem.
   Depois, tirar as marcas `FRAGILE`: ferramenta persistente documentada, API de prancha/PDF/SVG, cenas desfazíveis.
4. **Etapa 3 — fluxo de entrega.** Cenários S/M/L como variantes do mesmo modelo, diagrama de intervenções
   (adição/subtração/reforma), comparativo na mesma escala, tabela de área útil e pranchas bilíngues
   (EP01, EP02, EP03). Estilos de linha por etiqueta e blocos dinâmicos dependem do que o núcleo aceitar.

## Decisões que dependem de você

- Qual critério escolhe o lado da porta quando há parede dos dois lados (a regra acerta 4 de 6 sozinha).
- Confirmar (ou trazer a medida real de) pé-direito, esquadrias, guarda-corpo e travessas.
- O que o tracejado vermelho do PDF significa, e quem marca «reforma» no diagrama de intervenções.
- Mandar um `.skp` pequeno com 2 ou 3 blocos reais (porta, janela, bloco dinâmico) e as captura das opções de linha.

## Licença e segurança

Tudo GPL-3.0-or-later, sem dependência nova (o `shapely` só foi usado em análises de apoio fora do projeto).
Nenhum arquivo de cliente entra no repositório (a pasta `modelos/` é ignorada).
