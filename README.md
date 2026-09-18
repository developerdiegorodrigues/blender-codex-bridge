# Blender Codex Bridge

Ponte local e auditavel entre o Codex e o Blender. O projeto usa a API Python
do Blender (`bpy`) para operacoes estruturadas; automacao visual por mouse e
teclado fica reservada para uma etapa posterior.

## Estado atual

- contrato inicial de comandos e respostas;
- bootstrap local para Blender 4.x com nucleo recarregavel;
- servidor HTTP restrito a `127.0.0.1`;
- autenticacao por token local;
- fila que executa chamadas `bpy` somente na thread principal do Blender;
- parada de emergencia por arquivo sentinela;
- cliente CLI para diagnostico, parada, retomada e inspecao da cena.
- criacao de hemisferios fechados para detalhes como pupilas;
- conversao do canal alfa de PNGs em relevos 3D reutilizaveis.
- deploy atomico do nucleo, com recarga e rollback sem reiniciar o Blender.

O adaptador MCP sera construido sobre este protocolo depois que a comunicacao
com o Blender estiver validada.

## Inicio rapido

Crie e ative um ambiente virtual, depois instale o cliente em modo editavel:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

### Instalar na sessao atual do Blender 4.0

Primeiro gere o pacote. O diretorio `dist/` nao e versionado, entao um clone
sempre precisa construi-lo a partir do fonte:

```bash
mkdir -p dist
(cd addon && zip -r ../dist/blender-codex-bridge-addon-0.4.0.zip blender_codex_bridge -x '*__pycache__*')
```

No Blender aberto:

1. Salve o modelo atual antes dos testes.
2. Abra `Edit > Preferences > Add-ons`.
3. Clique em `Install...`.
4. Selecione `dist/blender-codex-bridge-addon-0.4.0.zip`.
5. Marque **Interface: Blender Codex Bridge** para habilitar o add-on.

Nao e necessario reiniciar o Blender. Ao marcar o add-on, ele inicia o servidor
local e grava os dados de conexao no diretorio temporario do usuario.

Com o add-on ativo:

```bash
blender-agent status
blender-agent deploy
blender-agent reload
blender-agent rollback
blender-agent scene
blender-agent transform --name Cube --location 0 0 1
blender-agent undo
blender-agent checkpoint before-change
blender-agent capture
blender-agent hemisphere --name Pupil.L \
  --location 0.075 -0.205 0.22 --radius 0.012 --direction=-Y
blender-agent relief --name Back.Symbol --image /caminho/simbolo.png \
  --location 0 0.12 -0.10 --normal 0 1 0.16 --up 0 -0.16 1 \
  --width 0.09 --depth 0.006 --resolution 512
blender-agent stop
blender-agent resume
```

O comando de parada cria uma sentinela fora do processo do Blender. Enquanto
ela existir, o add-on rejeita novos comandos:

```bash
blender-agent stop
# alternativa independente do cliente:
touch /tmp/blender-codex-bridge-$(id -u)/STOP
```

`Ctrl+C` continua interrompendo o cliente no terminal, mas nao e tratado como
o mecanismo principal de emergencia porque o foco pode estar no Blender.

## Atualizacoes sem a interface do Blender

Desde a versao 0.4, o arquivo `__init__.py` e um bootstrap estavel e as
operacoes de modelagem ficam em `core.py`. Para publicar uma alteracao do
nucleo na sessao aberta:

```bash
blender-agent deploy
```

O cliente valida a sintaxe, calcula SHA-256 e grava uma release imutavel no
diretorio de runtime. O bootstrap confere o caminho, checksum, versao da API e
autoteste antes de trocar o nucleo ativo. Uma falha preserva a versao anterior.

```bash
blender-agent status    # mostra versoes do bootstrap e do nucleo
blender-agent reload    # recarrega a release atual
blender-agent rollback  # volta atomicamente para a release anterior
```

Alteracoes no proprio bootstrap continuam exigindo reinstalacao ou uma recarga
manual do add-on, mas devem ser raras.

## Configuracao

As tres variaveis abaixo devem ser iguais no Blender e no terminal:

- `BLENDER_CODEX_HOST` (padrao: `127.0.0.1`);
- `BLENDER_CODEX_PORT` (padrao: `9876`);
- `BLENDER_CODEX_RUNTIME_DIR` (padrao: diretorio temporario do usuario).

O token e os dados de conexao ficam no diretorio de runtime com permissao
restrita ao usuario.

## Modelo de seguranca

O servidor so aceita `127.0.0.1` ou `localhost`; qualquer outro valor de
`BLENDER_CODEX_HOST` e recusado na inicializacao. O token tem 32 bytes, e
comparado em tempo constante e fica em um arquivo `0600`, dentro de um
diretorio de runtime `0700`.

O token nao e uma sandbox. Quem consegue le-lo executa Python dentro do seu
Blender: a acao `reload_core` importa um modulo do diretorio de releases e o
executa no processo. As travas existentes limitam a origem do modulo -- ele
precisa estar dentro de `releases/`, casar com um SHA-256 informado, ter no
maximo 2 MiB e passar no autoteste -- mas nao restringem o que o codigo faz
depois de carregado. Trate o token como equivalente a execucao de codigo local
com o seu usuario, e nao exponha o servidor em uma interface de rede.

## Testes

```bash
python3 -m unittest discover -s tests -v
```

Consulte [PLAN.md](PLAN.md) para as fases, criterios de aceite e decisoes de
seguranca.

## Licenca

MIT. Veja [LICENSE](LICENSE).
