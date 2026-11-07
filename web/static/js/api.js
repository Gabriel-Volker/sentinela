/**
 * api.js — Camada de comunicação com o backend.
 * Sem regras de negócio. Retorna promessas.
 */

const API = (() => {
  const BASE = '';

  async function _req(method, path, body) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (body !== undefined) {
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(BASE + path, opts);
    const data = await resp.json();
    if (!resp.ok) {
      const msg = data.mensagem || `HTTP ${resp.status}`;
      throw new Error(msg);
    }
    return data.dados;
  }

  return {
    health: () => _req('GET', '/health'),
    listarCasos: () => _req('GET', '/casos'),
    catalogoBuscar: (q, pagina) => {
      const qs = new URLSearchParams();
      if (q) qs.set('q', q);
      if (pagina) qs.set('pagina', String(pagina));
      return _req('GET', `/catalogo?${qs}`);
    },
    comprasDe: (clienteId) => _req('GET', `/catalogo?q=${encodeURIComponent(clienteId)}`),
    listarComprasCliente: (clienteId) => {
      // Busca candidatas do cliente via contexto (a API de catalogo filtra por texto)
      // O backend retorna clientes; para compras usamos contexto
      return _req('GET', `/contexto/_candidatas/${clienteId}`);
    },
    contexto: (txId) => _req('GET', `/contexto/${txId}`),
    obterCaso: (txId) => _req('GET', `/casos/${txId}`),
    analisar: (txId, modo) => _req('POST', `/casos/${txId}/analisar`, { modo }),
    responder: (txId, texto, semResposta) =>
      _req('POST', `/casos/${txId}/responder`, { texto: texto || null, sem_resposta: semResposta }),
    simular: (txId) => _req('POST', `/casos/${txId}/simular`, {}),
    consultarRun: (txId) => _req('POST', `/casos/${txId}/consultar`, {}),
    exportar: () => _req('GET', '/exportar'),
  };
})();
