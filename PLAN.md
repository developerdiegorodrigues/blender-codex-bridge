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

### Fase 1 - Fundacao (em andamento)

- [x] Estrutura independente do projeto.
- [x] Protocolo versionado de comandos e respostas.
- [x] Runtime local, token, arquivo de conexao e sentinela de parada.
- [x] Cliente CLI basico.
- [x] Esqueleto do add-on com fila para a thread principal.
- [x] Primitiva de hemisferio fechado para detalhes do modelo.
- [ ] Instalar o add-on e validar a conexao em uma sessao grafica do Blender.
- [ ] Validar `get_scene_summary`, transformacao, undo e checkpoint.

**Criterio de aceite:** o cliente consulta uma cena aberta, move um objeto,
desfaz a alteracao e interrompe novos comandos sem fechar o Blender.

### Fase 2 - Evidencia visual

- [ ] Captura confiavel do viewport ativo.
- [ ] Render de baixa resolucao como fallback.
- [ ] Metadados da captura: camera, resolucao, horario e caminho.
- [ ] Ferramenta que aguarda estabilizacao da cena antes da captura.

**Criterio de aceite:** cada lote de alteracoes pode produzir uma imagem que o
Codex consegue abrir e comparar com o objetivo.

### Fase 3 - Adaptador MCP

- [ ] Expor cada acao como ferramenta MCP tipada.
- [ ] Adicionar descricoes, timeouts e classificacao leitura/escrita.
- [ ] Configurar o servidor MCP no Codex.
- [ ] Exigir confirmacao para salvamento e operacoes de alto impacto.

**Criterio de aceite:** o Codex chama as ferramentas diretamente, sem montar
requisicoes HTTP ou comandos de shell manualmente.

### Fase 4 - Operacoes de modelagem

- [ ] Criacao e exclusao controlada de objetos.
- [ ] Materiais, luzes, cameras e colecoes.
- [ ] Importacao/exportacao com caminhos permitidos.
- [ ] Operacoes em lote com checkpoint e rollback.

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

## Proxima sessao pratica

1. Empacotar e instalar o add-on no Blender 4.0.2 local.
2. Abrir a cena padrao e executar `blender-agent status` e `scene`.
3. Testar uma transformacao seguida de `undo`.
4. Acionar `stop`, confirmar a rejeicao e executar `resume`.
5. Registrar incompatibilidades encontradas antes de iniciar o MCP.
