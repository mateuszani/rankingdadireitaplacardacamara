# Radar NasRuas Câmara Federal

O que cada um dos 513 deputados federais fez, só com dados oficiais da Câmara dos Deputados:
presença no plenário, propostas separadas em grupos fáceis de entender, votos, com quem cada um vota junto,
quanto custou o mandato, as emendas já pagas por setor, uma nota de 0 a 10 com regra pública, o comparativo entre dois deputados
e o Custômetro com o gasto da Câmara no ano.
Um projeto do **movimento NasRuas**.

**Site:** https://mateuszani.github.io/rankingdadireitaplacardacamara/

## O robô

Todo dia às 03:17 (horário de Brasília) o GitHub roda `robo/rodar.py`, que:

1. baixa de novo a lista de deputados, os arquivos de votações e de proposições dos Dados Abertos da Câmara
   (em pedaços de 1 MB, conferindo o tamanho de cada arquivo), a página oficial de cada deputado (presença, propostas,
   cota parlamentar e verba de gabinete), a lista oficial de emendas de cada deputado e o gasto do ano na página de transparência;
2. calcula tudo o que o site mostra;
3. **confere** a coleta antes de publicar. O site **não** é publicado se:
   - a lista de deputados em exercício não tiver perto de 513 nomes;
   - alguma página oficial de deputado não puder ser lida;
   - houver menos de 1.000 votações nominais, ou um ano passado sem nenhuma votação;
   - o placar de alguma votação principal não bater com o resultado oficial;
   - houver menos de 200.000 propostas, ou o total de propostas não bater com a página oficial em pelo menos 90% dos deputados;
   - alguma lista de emendas não puder ser lida ou vier com menos emendas do que a própria página anuncia;
   - os custos de alguma página não puderem ser lidos, ou menos de 480 deputados tiverem custo registrado;
   - o gasto da Câmara no ano não puder ser lido;
   - qualquer contagem (inclusive custo total dos mandatos e total de emendas pagas) cair mais de 1% em relação à última versão publicada, ou o número de votações de um ano passado diminuir;
4. só se tudo passar, monta o site em `saida/` e publica no GitHub Pages.

Se a conferência falhar, a rodada aparece com ❌ na aba **Actions**, com a lista dos problemas,
e o site continua com a última versão boa. Para rodar na hora: aba **Actions** → *Atualizar o Radar NasRuas Câmara Federal* → **Run workflow**
(desmarque *Publicar* para só testar: o resultado fica no artefato `teste`).

## Arquivos

- `robo/rodar.py`: coleta, cálculos, conferência e montagem do site.
- `robo/baixar.py`: download com conferência de bytes.
- `robo/custos.py`: custos do mandato, salário pelo Decreto Legislativo 172/2022, emendas, setor de cada emenda e gasto da Câmara.
- `robo/classificar.py`: a regra única que separa as propostas em grupos.
- `robo/titulos_votacoes.json`: resumos em linguagem simples das votações principais (as novas usam a ementa oficial).
- `modelo/index.html`: o site, com marcadores que o robô preenche.

Fontes: dadosabertos.camara.leg.br e camara.leg.br.
