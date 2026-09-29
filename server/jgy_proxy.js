const http = require('http');
const https = require('https');
const crypto = require('crypto');

const TARGET_HOST = 'jgy.gszy.edu.cn';
const PORT = 18888;

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

  // Normalize user-agent
  if (reqHeaders['user-agent']) {
    reqHeaders['user-agent'] = reqHeaders['user-agent'].replace(/HeadlessChrome\/[\d.]+/g, 'Chrome/128.0.0.0');
  }

  console.log(`[REQ] ${req.method} ${req.url}`);
  const proxyReq = https.request(options, (proxyRes) => {
    console.log(`[RES] ${proxyRes.statusCode} for ${req.url}`);
    const resHeaders = { ...proxyRes.headers };

    // Remove secure flag from cookies so http://localhost can store them
    if (resHeaders['set-cookie']) {
      resHeaders['set-cookie'] = resHeaders['set-cookie'].map(c => 
        c.replace(/;\s*Secure/gi, '').replace(/;\s*SameSite=None/gi, '; SameSite=Lax')
      );
    }

    if (resHeaders['location']) {
      resHeaders['location'] = resHeaders['location'].replace('https://' + TARGET_HOST, 'http://127.0.0.1:' + PORT)
                                                     .replace('http://' + TARGET_HOST, 'http://127.0.0.1:' + PORT);
    }

    // If HTML response (like 412), inject stealth script
    const contentType = resHeaders['content-type'] || '';
    if (contentType.includes('text/html')) {
      const chunks = [];
      proxyRes.on('data', c => chunks.push(c));
      proxyRes.on('end', () => {
        let html = Buffer.concat(chunks).toString('utf-8');
        const stealth = `<script>
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
</script>`;
        html = html.replace('<head>', '<head>' + stealth);
        delete resHeaders['content-length'];
        delete resHeaders['transfer-encoding'];
        res.writeHead(proxyRes.statusCode, resHeaders);
        res.end(html);
      });
      return;
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
  console.log(`JGY TLS Forwarder listening on http://127.0.0.1:${PORT}`);
});
