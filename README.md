# Blender Codex Bridge

Ponte local e auditavel entre o Codex e o Blender. O projeto usa a API Python
do Blender (`bpy`) para operacoes estruturadas; automacao visual por mouse e
teclado fica reservada para uma etapa posterior.

## Estado atual

- contrato inicial de comandos e respostas;
- add-on local para Blender 4.x;
- servidor HTTP restrito a `127.0.0.1`;
- autenticacao por token local;
- fila que executa chamadas `bpy` somente na thread principal do Blender;
- parada de emergencia por arquivo sentinela;
- cliente CLI para diagnostico, parada, retomada e inspecao da cena.
- criacao de hemisferios fechados para detalhes como pupilas.

O adaptador MCP sera construido sobre este protocolo depois que a comunicacao
com o Blender estiver validada.

## Inicio rapido

Crie e ative um ambiente virtual, depois instale o cliente em modo editavel:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

### Instalar na sessao atual do Blender 4.0

Primeiro gere o pacote (um pacote pronto tambem pode existir em `dist/`):

```bash
mkdir -p dist
(cd addon && zip -r ../dist/blender-codex-bridge-addon-0.2.0.zip blender_codex_bridge -x '*__pycache__*')
```

No Blender aberto:

1. Salve o modelo atual antes dos testes.
2. Abra `Edit > Preferences > Add-ons`.
3. Clique em `Install...`.
4. Selecione `dist/blender-codex-bridge-addon-0.2.0.zip`.
5. Marque **Interface: Blender Codex Bridge** para habilitar o add-on.

Nao e necessario reiniciar o Blender. Ao marcar o add-on, ele inicia o servidor
local e grava os dados de conexao no diretorio temporario do usuario.

Com o add-on ativo:

```bash
blender-agent status
blender-agent scene
blender-agent checkpoint before-change
blender-agent capture
blender-agent hemisphere --name Pupil.L \
  --location 0.075 -0.205 0.22 --radius 0.012 --direction=-Y
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

## Configuracao

As tres variaveis abaixo devem ser iguais no Blender e no terminal:

- `BLENDER_CODEX_HOST` (padrao: `127.0.0.1`);
- `BLENDER_CODEX_PORT` (padrao: `9876`);
- `BLENDER_CODEX_RUNTIME_DIR` (padrao: diretorio temporario do usuario).

O token e os dados de conexao ficam no diretorio de runtime com permissao
restrita ao usuario. Nao exponha o servidor em uma interface de rede.

## Testes

```bash
python3 -m unittest discover -s tests -v
```

Consulte [PLAN.md](PLAN.md) para as fases, criterios de aceite e decisoes de
seguranca.
