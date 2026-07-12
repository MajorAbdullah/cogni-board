const http = require('http');
const fs = require('fs');
const path = require('path');
const dir = path.join(__dirname);
const types = {'.html':'text/html','.css':'text/css','.js':'application/javascript','.svg':'image/svg+xml','.png':'image/png'};
http.createServer((req, res) => {
  let f = path.join(dir, req.url === '/' ? 'auth.html' : req.url);
  const ext = path.extname(f);
  fs.readFile(f, (err, data) => {
    if (err) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, {'Content-Type': types[ext] || 'text/plain'});
    res.end(data);
  });
}).listen(9000, () => console.log('Serving design-prototype on http://localhost:9000'));
