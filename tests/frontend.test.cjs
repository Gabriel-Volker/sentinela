const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function promessa() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}

async function interfaceReal() {
  const elementos = new Map();
  const renders = [];
  const timers = new Map();
  let bootstrap, selecionar, timerId = 0;
  const consulta = promessa();
  const consultaManual = promessa();
  let consultas = 0;
  const resposta = promessa();
  const inicial = { transacao_id: 'TX-TESTE', estado: 'pendente_execucao', revisao: 2 };
  const API = {
    health: async () => ({}), catalogoBuscar: async () => ({ clientes: [{ id: 'c1' }] }),
    listarComprasCliente: async () => ({ candidatas: [{ id: 'TX-TESTE' }] }),
    listarCasos: async () => [inicial], obterCaso: async () => inicial,
    consultarRun: () => (++consultas === 1 ? consulta.promise : consultaManual.promise), responder: () => resposta.promise,
  };
  const UI = new Proxy({
    qs(selector) {
      if (!elementos.has(selector)) elementos.set(selector, {
        value: 'Sim, fui eu.', disabled: false, handlers: {},
        addEventListener(nome, fn) { this.handlers[nome] = fn; },
        scrollIntoView() {}, focus() {},
      });
      return elementos.get(selector);
    },
    renderizarListaCompras(a, b, c, fn) { selecionar = fn; },
    renderizarResultado(caso) { if (caso) renders.push(JSON.parse(JSON.stringify(caso))); },
  }, { get(obj, key) { return obj[key] || (() => {}); } });
  const contexto = vm.createContext({
    API, UI, document: { addEventListener(nome, fn) { bootstrap = fn; } },
    window: { matchMedia: () => ({ matches: false }) },
    setTimeout(fn) {
      const id = ++timerId;
      timers.set(id, () => { timers.delete(id); return fn(); });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(readFileSync(path.join(__dirname, '../web/static/js/app.js'), 'utf8'), contexto);
  await bootstrap();
  await selecionar('TX-TESTE');
  return { elementos, renders, timers, consulta, consultaManual, resposta };
}

test('poll em trânsito não apaga resposta otimista nem reabre a pergunta anterior', async () => {
  const tela = await interfaceReal();
  const tick = tela.timers.values().next().value;
  const poll = tick(); // aguarda o retorno externo
  // Uma consulta manual mais recente entrega a primeira pergunta.
  const manual = tela.elementos.get('#btn-consultar-run').handlers.click();
  const pergunta = { transacao_id: 'TX-TESTE', estado: 'aguardando_cliente', revisao: 3,
    conversa: [{ papel: 'agente', texto: 'Reconhece?' }] };
  tela.consultaManual.resolve(pergunta);
  await manual;
  const envio = tela.elementos.get('#btn-enviar-resposta').handlers.click();
  tela.consulta.resolve(pergunta);
  await poll;
  const otimista = tela.renders.at(-1);
  assert.equal(otimista.estado, 'validando');
  assert.equal(otimista.conversa.at(-1).papel, 'cliente');
  tela.resposta.resolve({ transacao_id: 'TX-TESTE', estado: 'autorizado', revisao: 5 });
  await envio;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(tela.renders.at(-1).estado, 'autorizado');
  assert.equal(tela.timers.size, 0);
});

test('falha terminal encerra o polling e mostra o diagnóstico do backend', async () => {
  const tela = await interfaceReal();
  const poll = tela.timers.values().next().value();
  tela.consulta.resolve({ transacao_id: 'TX-TESTE', estado: 'pendente', revisao: 3,
    falha_integracao: { fase: 'analise', codigo: 'failed', mensagem: 'Revisão humana necessária.' } });
  await poll;
  assert.equal(tela.renders.at(-1).estado, 'pendente');
  assert.equal(tela.timers.size, 0);
  const contexto = vm.createContext({});
  vm.runInContext(readFileSync(path.join(__dirname, '../web/static/js/ui.js'), 'utf8'), contexto);
  contexto.caso = tela.renders.at(-1);
  const descricao = vm.runInContext('UI.descreverResultado(caso, null)', contexto);
  assert.equal(descricao.texto, 'Revisão humana necessária.');
  assert.equal(descricao.carregando, undefined);
});
