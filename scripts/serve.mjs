import { createServer } from 'node:http'
import server from '../dist/server/server.js'

const host = process.env.HOST || '127.0.0.1'
const port = Number(process.env.PORT || 3000)

const httpServer = createServer(async (incoming, outgoing) => {
  try {
    const origin = `http://${incoming.headers.host || `${host}:${port}`}`
    const method = incoming.method || 'GET'
    const init = {
      method,
      headers: incoming.headers,
      ...(method === 'GET' || method === 'HEAD'
        ? {}
        : { body: incoming, duplex: 'half' }),
    }
    const request = new Request(new URL(incoming.url || '/', origin), init)
    const response = await server.fetch(request)
    outgoing.writeHead(response.status, Object.fromEntries(response.headers))
    outgoing.end(Buffer.from(await response.arrayBuffer()))
  } catch (error) {
    console.error('Request handling failed:', error)
    if (!outgoing.headersSent) outgoing.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' })
    outgoing.end('Internal Server Error')
  }
})

httpServer.listen(port, host, () => {
  console.log(`Smart Slots is listening on http://${host}:${port}`)
})
