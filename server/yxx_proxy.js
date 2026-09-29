const http = require('http');
const https = require('https');
const crypto = require('crypto');

const TARGET_HOST = 'yxx.gszy.edu.cn';
const PORT = 18889;

const server = http.createServer((req, res) => {
  const reqHeaders = { ...req.headers };
  reqHeaders['host'] = TARGET_HOST;
  delete reqHeaders['accept-encoding']; // plain text for easy handling

  const options = {
    hostname: TARGET_HOST,
    port: 443,
    path: req.url,
    method: req.method,
    rejectUnauthorized: false,
    secureOptions: crypto.constants.SSL_OP_ALLOW_UNSAFE_LEGACY_RENEGOTIATION,
    headers: reqHeaders
  };

  console.log(`[REQ] ${req.method} ${req.url} Cookie: ${req.headers['cookie'] || 'none'}`);
  const proxyReq = https.request(options, (proxyRes) => {
    console.log(`[RES] ${proxyRes.statusCode} for ${req.url}`);
    const resHeaders = { ...proxyRes.headers };

    // Remove secure flag from cookies so http://localhost can store them
    if (resHeaders['set-cookie']) {
      resHeaders['set-cookie'] = resHeaders['set-cookie'].map(c => 
        c.replace(/;\s*Secure/gi, '').replace(/;\s*SameSite=None/gi, '; SameSite=Lax')
      );
    }

    // Rewrite redirects to localhost
    if (resHeaders['location']) {
      resHeaders['location'] = resHeaders['location'].replace('https://' + TARGET_HOST, 'http://127.0.0.1:' + PORT)
                                                     .replace('http://' + TARGET_HOST, 'http://127.0.0.1:' + PORT);
    }

    res.writeHead(proxyRes.statusCode, resHeaders);
    proxyRes.pipe(res);
  });

  proxyReq.on('error', (err) => {
    console.error('Proxy Error:', err.message);
    if (!res.headersSent) {
      res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('Proxy Error: ' + err.message);
    }
  });

  req.pipe(proxyReq);
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`YXX TLS Forwarder listening on http://127.0.0.1:${PORT}`);
});
