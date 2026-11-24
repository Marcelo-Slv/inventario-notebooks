# Inventário de Notebooks | Patrimônio TI

Painel profissional de consulta e gestão do inventário de notebooks. Os dados da planilha são importados para um banco SQLite e exibidos em um dashboard público, sem necessidade de login. Alterações no banco exigem autenticação.

**Nenhuma credencial, senha ou planilha está gravada neste repositório.**

## Antes de publicar no GitHub

1. Confira se `.gitignore` está presente. Ele bloqueia `.env`, `*.xlsx`, `*.enc`, `inventario.db`, chaves e logs.
2. **Nunca** envie `.env`, `inventario.db`, a planilha original ou qualquer arquivo de `dados/`.
3. Rode o comando de verificação antes do `git add`:

```powershell
py python/verificar_publicacao.py
```

Se ele acusar algum arquivo sensível, remova-o do stage do Git antes de continuar.

## Configuração local (primeira execução)

É necessário Python 3.10 ou superior. A lógica do site usa apenas a biblioteca padrão; a criptografia da planilha exige `cryptography`.

```powershell
py -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edite o `.env` e preencha `LOGIN_PROFESSOR`, `SENHA_PROFESSOR`, `LOGIN_ADMIN`, `SENHA_ADMIN` e `SENHA_PLANILHA`. Se você não criar o `.env`, o sistema gera credenciais aleatórias no primeiro acesso e as grava somente no `.env` local.

### Protegendo a planilha

A planilha original deve ser convertida em um cofre criptografado (AES-GCM com chave derivada por PBKDF2):

```powershell
py python/proteger_planilha.py proteger
```

O arquivo é salvo em `dados/inventario.xlsx.enc`. Configure no `.env`:

```
PLANILHA_PATH=dados/inventario.xlsx.enc
SENHA_PLANILHA=<sua senha local>
```

Depois de validar a importação, **exclua a planilha XLSX em texto aberto da pasta do projeto**. Para restaurar uma cópia local:

```powershell
py python/proteger_planilha.py desproteger
```

## Executar

No Windows, basta dar dois cliques em `iniciar.bat`. Pelo terminal:

```powershell
py python/servidor.py
```

Acesse `http://127.0.0.1:8000`. O banco `inventario.db` é criado automaticamente e recebe os registros do cofre da planilha. Para usar outra planilha, atualize o cofre e use "Reimportar planilha" com o login de administrador.

## Acessos

Os usuários e senhas ficam **somente no `.env` local**, nunca no código ou neste arquivo. Perfis:

| Perfil | Permissões |
| --- | --- |
| Professor | Cadastrar e editar equipamentos |
| Administrador | Cadastrar, editar, excluir, reimportar e exportar planilhas |

A consulta ao dashboard e à lista de equipamentos é pública. A área de gestão fica oculta até o login. As sessões expiram após 8 horas. Após 5 falhas de login no mesmo endereço, o acesso fica bloqueado por 15 minutos.

As exportações (XLSX e CSV) ficam somente no painel administrativo; o professor não vê nem acessa os botões ou endpoints.

## Estrutura

```
index.html                 Estrutura do site
css/styles.css             Estilo visual
js/app.js                  Dashboard, filtros, autenticação e formulários
python/banco.py            Banco SQLite, importação da planilha e consultas
python/servidor.py         Servidor HTTP e API
python/config.py           Leitura do .env local
python/seguranca.py        Hashes de senha e cofre criptografado
python/proteger_planilha.py  Ferramenta de criptografia da planilha
python/verificar_publicacao.py  Checagem antes do commit
.env.example               Modelo de configuração (sem segredos)
iniciar.bat                Atalho de execução no Windows
```

`inventario.db`, `.env`, `dados/` e planilhas são locais e ignorados pelo Git.

## Métricas do dashboard

- **Funcionando:** status funcional preenchido como "Funcionando".
- **Não funcionando:** "Com defeito", "Não liga" ou "Aguardando reparo".
- **Disponíveis para uso:** funcionando, com carregador incluso e estado físico "Bom" ou "Regular".
- **Em manutenção:** aguardando reparo.
- **Sem informação:** registros cujo status não foi preenchido.
- Linhas reservadas apenas com o código da etiqueta e sem dados de levantamento são ignoradas.

## Segurança implementada

- Senhas com PBKDF2-HMAC-SHA256, salt aleatório e 600 mil iterações, comparadas em tempo constante. Renovação automática de hashes antigos no login.
- Planilha protegida com AES-GCM e chave derivada por PBKDF2; o arquivo publicado não contém os dados.
- Sessões em cookie `HttpOnly`, `SameSite=Lax`, com `Secure` automático quando `HTTPS=1`.
- Limite de tentativas de login e bloqueio temporário por IP.
- Verificação de `Origin` e `Sec-Fetch-Site` contra CSRF nas rotas que alteram dados.
- CSP, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` e `Cross-Origin-*`.
- Escape de HTML em todo dado dinâmico exibido no front-end (proteção contra XSS).
- Sem detalhes de exceções nas respostas da API.
- Somente arquivos estáticos permitidos são servidos; `.env`, banco, cofre e planilhas não são acessíveis pela web.
- Corpo JSON limitado a 1 MB e tipo de conteúdo obrigatório.

Para exposição em rede, configure HTTPS (ou um proxy TLS), defina `HTTPS=1` e troque as credenciais locais. Este servidor é adequado para uso local/intranet; para internet, use um servidor/proxy reverso com TLS e controle de acesso na borda.
