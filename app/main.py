<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>LICITABOT — Acesso</title>
  <style>
    :root {
      --azul: #0b3d5c;
      --cinza: #475569;
      --fundo: #f1f5f9;
    }
    body {
      font-family: Arial, Helvetica, sans-serif;
      background: var(--fundo);
      margin: 0;
      height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .caixa {
      background: #fff;
      border-radius: 16px;
      box-shadow: 0 10px 28px rgba(0, 0, 0, 0.12);
      width: 100%;
      max-width: 420px;
      padding: 32px;
      text-align: center;
    }
    h1 {
      color: var(--azul);
      margin-bottom: 8px;
      font-size: 22px;
    }
    p {
      color: var(--cinza);
      margin: 0 0 20px;
      font-size: 15px;
    }
    input {
      width: 100%;
      padding: 12px;
      border-radius: 10px;
      border: 1px solid #cbd5e1;
      font-size: 15px;
      margin-bottom: 14px;
      box-sizing: border-box;
    }
    button {
      width: 100%;
      background: var(--azul);
      color: #fff;
      border: none;
      padding: 12px;
      border-radius: 10px;
      font-weight: bold;
      font-size: 15px;
      cursor: pointer;
    }
    button:hover {
      filter: brightness(1.05);
    }
    #msg {
      margin-top: 12px;
      font-weight: 600;
    }
    .ok { color: #166534; }
    .err { color: #991b1b; }
  </style>
</head>
<body>
  <div class="caixa">
    <h1>LICITABOT — Acesso</h1>
    <p>Área exclusiva para assinantes.<br>Digite sua senha para continuar.</p>
    <input id="senha" type="password" placeholder="Senha de acesso" />
    <button onclick="entrar()">Entrar</button>
    <div id="msg"></div>
  </div>

  <script>
    async function entrar() {
      const senha = document.getElementById('senha').value.trim();
      const msg = document.getElementById('msg');
      msg.innerHTML = '';
      if (!senha) {
        msg.innerHTML = '<p class="err">Informe a senha.</p>';
        return;
      }
      try {
        const resp = await fetch('/login', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({password: senha})
        });
        const data = await resp.json();
        if (data.ok) {
          msg.innerHTML = '<p class="ok">✅ Acesso liberado...</p>';
          setTimeout(() => { window.location.href = '/chat'; }, 800);
        } else {
          msg.innerHTML = '<p class="err">❌ ' + (data.error || 'Senha incorreta.') + '</p>';
        }
      } catch (e) {
        msg.innerHTML = '<p class="err">Erro de conexão.</p>';
      }
    }
  </script>
</body>
</html>
