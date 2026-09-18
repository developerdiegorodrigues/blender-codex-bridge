# Plano de implementacao

## Objetivo

Permitir que o Codex inspecione e altere uma cena do Blender por ferramentas
pequenas, tipadas e auditaveis, recebendo imagens para verificacao visual e
mantendo um mecanismo de parada independente do foco da interface.

## Principios

1. Preferir `bpy` a coordenadas de tela.
2. Nunca chamar `bpy` a partir da thread do servidor HTTP.
3. Escutar apenas em `127.0.0.1` e exigir token.
4. Fazer checkpoints antes de lotes de alteracoes extensas.
5. Nao oferecer execucao arbitraria de Python na primeira versao.
6. Verificar a sentinela `STOP` entre comandos.
7. Limitar tempo, tamanho e quantidade de comandos.

## Fases

Legenda: `[x]` feito, `[~]` parcial (a lacuna exata vem descrita), `[ ]` nao
iniciado.

### Fase 1 - Fundacao (em andamento)

- [x] Estrutura independente do projeto.
- [x] Protocolo versionado de comandos e respostas.
- [x] Runtime local, token, arquivo de conexao e sentinela de parada.
- [x] Cliente CLI basico.
- [x] Esqueleto do add-on com fila para a thread principal.
- [x] Primitiva de hemisferio fechado para detalhes do modelo.
- [x] Vetorizacao do canal alfa de PNG para relevos 3D reutilizaveis.
- [x] Bootstrap estavel com deploy atomico, reload e rollback do nucleo.
- [x] `transform` e `undo` alcancaveis pelo cliente. Eram as duas unicas das
      21 acoes que o nucleo executava e o protocolo validava sem nenhum
      subcomando capaz de envia-las, o que tornava este criterio de aceite
      impossivel de exercitar.
- [x] Testes do bootstrap sem depender do Blender: confinamento da release,
      checksum, sufixo, limite de 2 MiB, versao de API, `self_test` e a cadeia
      de rollback.
- [ ] Instalar o add-on e validar a conexao em uma sessao grafica do Blender.
- [ ] Validar `get_scene_summary`, transformacao, undo e checkpoint.

As duas pendencias restantes exigem apenas um Blender aberto: nao falta mais
codigo para executa-las.

**Criterio de aceite:** o cliente consulta uma cena aberta, move um objeto,
desfaz a alteracao e interrompe novos comandos sem fechar o Blender.

### Fase 2 - Evidencia visual

- [~] Captura do viewport: `capture_viewport` existe, mas escolhe o primeiro
      `VIEW_3D` que encontra, e nao o ativo.
- [~] Fallback de render: `render_workbench_preview` cobre o caso, porem como
      acao separada. Falta a captura recorrer a ele sozinha quando nao ha
      `VIEW_3D` disponivel, em vez de falhar.
- [ ] Metadados da captura: hoje a resposta traz so `path` e `kind`. Faltam
      camera, resolucao e horario.
- [ ] Ferramenta que aguarda estabilizacao da cena antes da captura.

**Criterio de aceite:** cada lote de alteracoes pode produzir uma imagem que o
Codex consegue abrir e comparar com o objetivo.

### Fase 3 - Adaptador MCP

- [ ] Expor cada acao como ferramenta MCP tipada.
- [~] Descricoes, timeouts e classificacao leitura/escrita. A classificacao ja
      existe em `protocol.py` (`READ_ACTIONS`, `WRITE_ACTIONS`,
      `ADMIN_ACTIONS`, com as 21 acoes categorizadas), mas nenhuma politica a
      consulta: hoje ela so serve para montar `ALLOWED_ACTIONS`.
- [ ] Configurar o servidor MCP no Codex.
- [ ] Exigir confirmacao para salvamento e operacoes de alto impacto.

**Criterio de aceite:** o Codex chama as ferramentas diretamente, sem montar
requisicoes HTTP ou comandos de shell manualmente.

### Fase 4 - Operacoes de modelagem

- [~] Criacao e exclusao controlada de objetos. A criacao existe em varias
      formas (hemisferio, solido de perfil, parede, relevo de imagem e de
      texto); nao ha nenhuma acao que apague um objeto.
- [ ] Materiais, luzes, cameras e colecoes.
- [x] Importacao/exportacao com caminhos permitidos: `import_stl_assembly` e
      `export_mesh`, este confinado a `BLENDER_CODEX_EXPORT_DIR`, recusando
      caminho absoluto, `..` e sufixo diferente de `.stl`.
- [ ] Operacoes em lote com checkpoint e rollback. `save_checkpoint` existe;
      o lote, nao.

### Fase 5 - Automacao visual opcional

- [ ] Detectar X11 ou Wayland e escolher um backend compativel.
- [ ] Captura da janela do Blender, nao da area de trabalho inteira.
- [ ] Mouse/teclado com limite de acoes por lote.
- [ ] Hotkey global de emergencia (sugestao: `Ctrl+Alt+Esc`).
- [ ] Liberar teclas e botoes pressionados ao parar.

**Criterio de aceite:** o controlador executa no maximo dez eventos, captura a
tela novamente e para imediatamente ao receber a sentinela ou a hotkey.

## Riscos conhecidos

- Uma operacao atomica longa do Blender, como um render, pode terminar antes
  que a sentinela seja observada; a parada atua entre operacoes.
- Operadores `bpy.ops` dependem de contexto. Sempre que possivel usaremos a API
  de dados; operadores de viewport precisarao de `temp_override`.
- A automacao visual depende do compositor, escala da tela, foco e layout.
- Arquivos `.blend` devem ser tratados como dados do usuario: checkpoints sao
  copias e nunca substituem silenciosamente o arquivo aberto.

## Debito tecnico conhecido

Nao pertence a nenhuma fase, e nenhum item bloqueia as fases seguintes.

- A fila de comandos nao tem `maxsize`. O principio 7 pede limite de tempo,
  tamanho e quantidade; tempo (`COMMAND_TIMEOUT_SECONDS`) e tamanho
  (`MAX_REQUEST_BYTES`) estao aplicados, quantidade nao.
- `client.py` e `cli.py` nao tem cobertura de teste alem do parser e do guarda
  de alcancabilidade das acoes.
- A versao aparece em tres lugares que podem divergir: `pyproject.toml`,
  `bl_info`/`BOOTSTRAP_VERSION` no bootstrap e `CORE_VERSION` no nucleo. O
  nome do zip no README repete o numero a mao.

## Proxima sessao pratica

1. Empacotar e instalar o add-on no Blender 4.0.2 local.
2. Abrir a cena padrao e executar `blender-agent status` e `scene`.
3. Testar uma transformacao seguida de `undo`:
   `blender-agent transform --name Cube --location 0 0 1` e `blender-agent undo`.
4. Acionar `stop`, confirmar a rejeicao e executar `resume`.
5. Registrar incompatibilidades encontradas antes de iniciar o MCP.
