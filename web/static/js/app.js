/**
 * app.js — Lógica da aplicação Sentinela.
 * Coordena eventos do usuário, chamadas API e atualizações de UI.
 * Sem regras de negócio — a decisão é do backend e dos agentes.
 */

(function () {
  'use strict';

  // ---- Estado local da sessão ----------------------------------------
  // (sem persistência — reiniciar o servidor perde os casos)

  const estado = {
    personas: [],            // clientes do catálogo
    comprasPorPersona: {},   // { clienteId: [tx, ...] }
    indiceTx: {},            // { txId: { tx, personaId } }
    casos: {},               // { txId: caso } — espelho de GET /casos
    personaAtualId: null,
    txAtualId: null,
    semConexao: new Set(),   // txIds analisados sem agentes configurados
    digitando: false,        // agente de validação processando resposta
    pollTimer: null,
    geracoes: {},
  };

  // Intervalo do poll da interface (ms).
  // O backend responde imediatamente se o worker já atualizou o resultado;
  // o intervalo é apenas o atraso máximo para detectar mudanças no estado local.
  // Reduzido de 8000 para 3000 para acompanhar o intervalo máximo do worker (3s).
  const POLL_INTERVALO_MS = 3000;

  // ---- Bootstrap -----------------------------------------------------

  document.addEventListener('DOMContentLoaded', async () => {
    bindEventos();
    try {
      await API.health();
    } catch (_) {
      UI.notificar('Servidor Sentinela não está disponível.', 'erro');
      return;
    }
    await carregarPersonas();
    await sincronizarCasos();
    if (estado.personas.length) selecionarPersona(estado.personas[0].id);
  });

  function bindEventos() {
    UI.qs('#btn-analisar').addEventListener('click', iniciarAnalise);
    UI.qs('#btn-enviar-resposta').addEventListener('click', enviarResposta);
    UI.qs('#campo-resposta').addEventListener('keydown', e => {
      if (e.key === 'Enter' && e.ctrlKey) enviarResposta();
    });
    UI.qs('#btn-sem-resposta').addEventListener('click', enviarSemResposta);
    UI.qs('#btn-simular').addEventListener('click', usarRoteiro);
    UI.qs('#btn-consultar-run').addEventListener('click', consultarRun);
    UI.qs('#btn-exportar').addEventListener('click', exportarSessao);
  }

  // ---- Carregamento --------------------------------------------------

  async function carregarPersonas() {
    try {
      const res = await API.catalogoBuscar('', 1);
      estado.personas = res.clientes || [];
      await Promise.all(estado.personas.map(async p => {
        const r = await API.listarComprasCliente(p.id);
        const compras = (r.candidatas || []).slice().sort((a, b) => (b.data_hora || '').localeCompare(a.data_hora || ''));
        estado.comprasPorPersona[p.id] = compras;
        compras.forEach(tx => { estado.indiceTx[tx.id] = { tx, personaId: p.id }; });
      }));
    } catch (err) {
      UI.notificar('Falha ao carregar personas: ' + err.message, 'erro');
    }
  }

  async function sincronizarCasos() {
    try {
      const casos = await API.listarCasos();
      (casos || []).forEach(c => aplicarCaso(c));
    } catch (_) {
      // Sessão vazia ou erro temporário — não bloqueia o fluxo
    }
    renderizarListas();
  }

  // ---- Renderização geral --------------------------------------------

  function personaAtual() {
    return estado.personas.find(p => p.id === estado.personaAtualId) || null;
  }

  function txAtual() {
    return estado.indiceTx[estado.txAtualId]?.tx || null;
  }

  function contagemPorPersona() {
    const cont = {};
    Object.values(estado.casos).forEach(c => {
      if (c.estado === 'novo') return;
      const pid = estado.indiceTx[c.transacao_id]?.personaId;
      if (pid) cont[pid] = (cont[pid] || 0) + 1;
    });
    return cont;
  }

  function renderizarListas() {
    UI.renderizarPersonas(estado.personas, estado.personaAtualId, contagemPorPersona(), selecionarPersona);
    if (estado.personaAtualId) {
      UI.renderizarListaCompras(
        estado.comprasPorPersona[estado.personaAtualId] || [],
        estado.txAtualId,
        estado.casos,
        selecionarCompra,
      );
    }
    UI.renderizarResumoSessao(estado.casos);
  }

  function renderizarCaso() {
    const caso = estado.casos[estado.txAtualId] || { transacao_id: estado.txAtualId, estado: 'novo' };
    const persona = personaAtual();
    const est = caso.estado;

    UI.toggleOculto(UI.qs('#painel-acao'), est === 'novo');
    UI.qs('#btn-analisar').disabled = est !== 'novo';

    UI.renderizarResultado(caso, persona, { semConexao: estado.semConexao.has(caso.transacao_id) });
    UI.renderizarConversa(caso, persona, estado.digitando);

    const aguardando = est === 'aguardando_cliente' && !estado.digitando;
    UI.toggleOculto(UI.qs('#painel-resposta'), aguardando);
    UI.toggleOculto(UI.qs('#btn-simular'), caso.modo === 'roteiro');
    if (aguardando) habilitarResposta(true);

    // Poll para executar_pendente e para validando (thread de background ainda em execução)
    if (est === 'pendente_execucao' || est === 'validando') agendarPoll(caso.transacao_id); else cancelarPoll();
  }

  // ---- Seleção -------------------------------------------------------

  function selecionarPersona(id) {
    if (estado.personaAtualId === id) return;
    estado.personaAtualId = id;
    estado.txAtualId = null;
    cancelarPoll();
    UI.renderizarPerfil(personaAtual());
    UI.renderizarTransacao(null);
    UI.renderizarResultado(null);
    UI.renderizarConversa(null);
    renderizarListas();
  }

  async function selecionarCompra(txId) {
    estado.txAtualId = txId;
    estado.digitando = false;
    const tx = txAtual();
    UI.renderizarTransacao(tx);
    UI.renderizarComparacao(tx, personaAtual());
    renderizarListas();
    renderizarCaso();

    // Atualiza com o estado mais recente do backend
    const geracao = estado.geracoes[txId] || 0;
    try {
      const caso = await API.obterCaso(txId);
      if (estado.txAtualId !== txId) return;
      if ((estado.geracoes[txId] || 0) !== geracao) return;
      aplicarCaso(caso);
    } catch (_) { /* mantém o que já está na tela */ }

    if (window.matchMedia('(max-width: 900px)').matches) {
      UI.qs('#cartao-transacao').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function aplicarCaso(caso) {
    const anterior = estado.casos[caso.transacao_id];
    if (caso.revisao !== undefined && anterior?.revisao !== undefined && caso.revisao < anterior.revisao) return false;
    estado.casos[caso.transacao_id] = caso;
    if (caso.transacao_id === estado.txAtualId && !['validando', 'pendente_execucao'].includes(caso.estado)) estado.digitando = false;
    renderizarListas();
    if (caso.transacao_id === estado.txAtualId) renderizarCaso();
    return true;
  }

  function mostrarResultado() {
    UI.qs('#cartao-resultado').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function notificarDesfecho(caso) {
    const info = UI.descreverResultado(caso, personaAtual(), { semConexao: estado.semConexao.has(caso.transacao_id) });
    if (!info) return;
    const tipos = { baixo: 'ok', alto: 'erro', medio: 'aviso', pendente: 'aviso', processando: 'info' };
    UI.notificar(info.titulo, tipos[info.tom] || 'info');
  }

  // ---- Polling para execução remota pendente -------------------------

  function cancelarPoll() {
    if (estado.pollTimer) { clearTimeout(estado.pollTimer); estado.pollTimer = null; }
  }

  function agendarPoll(txId) {
    if (estado.pollTimer) return;
    estado.pollTimer = setTimeout(async () => {
      estado.pollTimer = null;
      if (estado.txAtualId !== txId) return;
      const geracao = estado.geracoes[txId] || 0;
      try {
        const caso = await API.consultarRun(txId);
        if ((estado.geracoes[txId] || 0) !== geracao) return;
        if (!aplicarCaso(caso) || estado.txAtualId !== txId) return;
        // Notifica quando sair de estados de espera (inclui validando e pendente_execucao)
        if (caso.estado !== 'pendente_execucao' && caso.estado !== 'validando') {
          notificarDesfecho(caso);
          if (caso.estado === 'aguardando_cliente') {
            UI.qs('#secao-conversa').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          }
        }
      } catch (_) {
        if (estado.txAtualId === txId) agendarPoll(txId);
      }
    }, POLL_INTERVALO_MS);
  }

  // ---- Análise -------------------------------------------------------

  async function iniciarAnalise() {
    const txId = estado.txAtualId;
    if (!txId) return;
    const modo = UI.qs('input[name="modo"]:checked')?.value || 'manual';

    estado.geracoes[txId] = (estado.geracoes[txId] || 0) + 1;
    cancelarPoll();
    // Feedback imediato enquanto o agente trabalha
    aplicarCaso({ transacao_id: txId, estado: 'analisando', modo });
    mostrarResultado();

    try {
      const caso = await API.analisar(txId, modo);
      // Sem agentes configurados o backend devolve o caso ainda em "analisando"
      if (caso.estado === 'analisando' && !caso.analise) estado.semConexao.add(txId);
      else estado.semConexao.delete(txId);
      aplicarCaso(caso);
      // Para risco médio: backend retorna pendente_execucao enquanto a validação
      // está em segundo plano. Não notifica desfecho ainda — aguarda poll.
      if (caso.estado !== 'pendente_execucao' && caso.estado !== 'validando') notificarDesfecho(caso);
      if (caso.estado === 'aguardando_cliente') {
        UI.qs('#secao-conversa').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    } catch (err) {
      UI.notificar('Falha na análise: ' + err.message, 'erro');
      await sincronizarCasos();
      if (estado.txAtualId === txId) renderizarCaso();
    }
  }

  // ---- Resposta do cliente -------------------------------------------

  function habilitarResposta(ativo) {
    ['#btn-enviar-resposta', '#btn-sem-resposta', '#btn-simular', '#campo-resposta'].forEach(sel => {
      const el = UI.qs(sel);
      if (el) el.disabled = !ativo;
    });
  }

  async function responder(texto, semResposta) {
    const txId = estado.txAtualId;
    const atual = estado.casos[txId];
    if (!txId || !atual || atual.estado !== 'aguardando_cliente' || estado.digitando) return;
    estado.geracoes[txId] = (estado.geracoes[txId] || 0) + 1;
    cancelarPoll();

    // Mostra a mensagem do cliente e o "digitando" do agente imediatamente
    habilitarResposta(false);
    estado.digitando = true;
    const turno = semResposta ? { papel: 'cliente', sem_resposta: true } : { papel: 'cliente', texto };
    aplicarCaso({ ...atual, estado: 'validando', conversa: [...(atual.conversa || []), turno] });

    try {
      const caso = await API.responder(txId, texto, semResposta);
      if (estado.txAtualId === txId) {
        estado.digitando = false;
        UI.qs('#campo-resposta').value = '';
      }
      aplicarCaso(caso);
      if (estado.txAtualId !== txId) return;
      if (caso.estado !== 'aguardando_cliente') {
        notificarDesfecho(caso);
        mostrarResultado();
      } else {
        UI.qs('#campo-resposta').focus();
      }
    } catch (err) {
      estado.digitando = false;
      UI.notificar('Falha ao enviar resposta: ' + err.message, 'erro');
      await sincronizarCasos();
      if (estado.txAtualId === txId) renderizarCaso();
    }
  }

  function enviarResposta() {
    const campo = UI.qs('#campo-resposta');
    const texto = campo.value.trim();
    if (!texto) {
      UI.notificar('Digite a resposta antes de enviar.', 'aviso');
      campo.focus();
      return;
    }
    responder(texto, false);
  }

  function enviarSemResposta() {
    responder(null, true);
  }

  async function usarRoteiro() {
    const txId = estado.txAtualId;
    if (!txId) return;
    try {
      const res = await API.simular(txId);
      if (res.fim_roteiro) {
        UI.notificar('O roteiro da persona acabou. Responda manualmente ou marque "Cliente não respondeu".', 'aviso');
        return;
      }
      if (res.fala !== null && res.fala !== undefined) {
        const campo = UI.qs('#campo-resposta');
        campo.value = res.fala;
        campo.focus();
      }
    } catch (err) {
      UI.notificar('Erro ao usar o roteiro: ' + err.message, 'erro');
    }
  }

  // ---- Consultar execução pendente -----------------------------------

  async function consultarRun() {
    const txId = estado.txAtualId;
    if (!txId) return;
    const geracao = estado.geracoes[txId] || 0;
    UI.mostrarSpinner('btn-consultar-run');
    try {
      const caso = await API.consultarRun(txId);
      if ((estado.geracoes[txId] || 0) !== geracao || !aplicarCaso(caso) || estado.txAtualId !== txId) return;
      if (caso.estado === 'pendente_execucao') UI.notificar('A execução ainda está em andamento.', 'aviso');
      else notificarDesfecho(caso);
    } catch (err) {
      UI.notificar('Falha ao consultar: ' + err.message, 'erro');
    } finally {
      UI.esconderSpinner('btn-consultar-run');
    }
  }

  // ---- Exportar sessão -----------------------------------------------

  async function exportarSessao() {
    try {
      const dados = await API.exportar();
      const blob = new Blob([JSON.stringify(dados, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `sentinela-sessao-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.json`;
      a.click();
      URL.revokeObjectURL(url);
      UI.notificar('Sessão exportada.', 'ok');
    } catch (err) {
      UI.notificar('Falha ao exportar: ' + err.message, 'erro');
    }
  }

})();
