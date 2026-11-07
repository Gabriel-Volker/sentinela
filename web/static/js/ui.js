/**
 * ui.js — Componentes de UI do Sentinela.
 * Manipula o DOM sem regras de negócio.
 * Dados vindos do backend são sempre inseridos via textContent (sem injeção de HTML).
 * innerHTML é usado apenas para ícones SVG constantes definidos neste arquivo.
 */

const UI = (() => {

  // ---- Utilitários DOM ------------------------------------------------

  function qs(sel, ctx = document) { return ctx.querySelector(sel); }
  function qsa(sel, ctx = document) { return [...ctx.querySelectorAll(sel)]; }

  function mostrar(el) { el && el.classList.remove('oculto'); }
  function ocultar(el) { el && el.classList.add('oculto'); }
  function toggleOculto(el, condicao) { condicao ? mostrar(el) : ocultar(el); }

  function criar(tag, cls, texto) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (texto !== undefined && texto !== null) el.textContent = texto;
    return el;
  }

  function icone(nome) {
    const span = criar('span', 'icone');
    span.setAttribute('aria-hidden', 'true');
    span.innerHTML = ICONES[nome] || '';
    return span;
  }

  const ICONES = {
    ok: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    bloqueio: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2.4"/><path d="m6 6 12 12" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>',
    chat: '<svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4V5Z" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"/><path d="M8.5 10.5h.01M12 10.5h.01M15.5 10.5h.01" stroke="currentColor" stroke-width="2.8" stroke-linecap="round"/></svg>',
    revisao: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.5" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
    alerta: '<svg viewBox="0 0 24 24"><path d="M12 3.5 2.5 20h19L12 3.5Z" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"/><path d="M12 10v4.5M12 17.2h.01" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>',
    info: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M12 11v5M12 7.8h.01" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>',
    escudo: '<svg viewBox="0 0 24 24"><path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3Z" fill="currentColor"/><path d="m8.5 12 2.5 2.5 4.5-5" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  // ---- Personas (apresentação) ----------------------------------------
  // Fotos e cores são só apresentação; os dados vêm de clientes.json.

  const PERSONAS = {
    CUST_SYN_0000001: { foto: '/static/img/persona-ana.svg', cor: '#2a9d8f' },
    CUST_SYN_0000002: { foto: '/static/img/persona-lucas.svg', cor: '#e8912f' },
    CUST_SYN_0000003: { foto: '/static/img/persona-roberto.svg', cor: '#22314a' },
  };

  function avatar(cliente, cls) {
    const cfg = PERSONAS[cliente?.id] || {};
    if (cfg.foto) {
      const img = document.createElement('img');
      img.className = 'avatar ' + (cls || '');
      img.src = cfg.foto;
      img.alt = `Foto de ${cliente.nome}`;
      img.width = 64;
      img.height = 64;
      return img;
    }
    const div = criar('div', 'avatar avatar-iniciais ' + (cls || ''), iniciais(cliente?.nome));
    div.setAttribute('aria-hidden', 'true');
    return div;
  }

  function iniciais(nome) {
    return (nome || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(p => p[0]).join('').toUpperCase();
  }

  function primeiroNome(cliente) {
    return (cliente?.nome || 'o cliente').split(/\s+/)[0];
  }

  // ---- Formatadores e rótulos -----------------------------------------

  function formatarMoeda(valor, moeda) {
    if (valor === null || valor === undefined || valor === '') return '—';
    try {
      return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: moeda || 'BRL' }).format(valor);
    } catch { return `${moeda || ''} ${valor}`; }
  }

  function formatarData(isoStr) {
    if (!isoStr) return '—';
    try {
      const d = new Date(isoStr);
      return d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
    } catch { return isoStr; }
  }

  function capitalizar(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }

  const CATEGORIAS = {
    supermercado: ['Supermercado', '🛒'],
    farmacia: ['Farmácia', '💊'],
    restaurante: ['Restaurante', '🍽️'],
    streaming: ['Streaming', '📺'],
    transporte: ['Transporte', '🚕'],
    assinatura_software: ['Assinatura de software', '💻'],
    lazer: ['Lazer', '🎟️'],
    viagem: ['Viagem', '✈️'],
    hotel: ['Hotel', '🏨'],
    eletronicos: ['Eletrônicos', '📱'],
    saude: ['Saúde', '🩺'],
    educacao: ['Educação', '🎓'],
    posto_combustivel: ['Posto de combustível', '⛽'],
    vestuario: ['Vestuário', '👕'],
  };

  function nomeCategoria(cat) { return CATEGORIAS[cat]?.[0] || capitalizar((cat || '').replace(/_/g, ' ')); }
  function emojiCategoria(cat) { return CATEGORIAS[cat]?.[1] || '💳'; }

  const CANAIS = { presencial: 'Presencial', online: 'Online', app_mobile: 'App mobile', telefone: 'Telefone' };
  function nomeCanal(canal) { return CANAIS[canal] || capitalizar(canal || ''); }

  const SEGMENTOS = { prime: 'Prime', universitario: 'Universitário', corporate: 'Corporate' };
  const RENDAS = { baixa: 'Renda baixa', media: 'Renda média', alta: 'Renda alta' };
  const RISCOS = { baixo: 'Baixo', medio: 'Médio', alto: 'Alto' };

  // Situação de cada caso em linguagem simples (lista de compras e cabeçalho)
  const SITUACAO = {
    analisando: ['Analisando', 'processando'],
    pendente_execucao: ['Analisando', 'processando'],
    validando: ['Em validação', 'medio'],
    aguardando_cliente: ['Em validação', 'medio'],
    autorizado: ['Liberada', 'baixo'],
    bloqueado: ['Bloqueada', 'alto'],
    pendente: ['Revisão humana', 'pendente'],
  };

  // ---- Seletor de personas --------------------------------------------

  function renderizarPersonas(personas, personaAtualId, contagens, onSelect) {
    const lista = qs('#lista-personas');
    lista.innerHTML = '';
    personas.forEach(p => {
      const btn = criar('button', 'persona-opcao');
      btn.type = 'button';
      btn.setAttribute('role', 'radio');
      btn.setAttribute('aria-checked', String(p.id === personaAtualId));
      btn.style.setProperty('--cor-persona', PERSONAS[p.id]?.cor || 'var(--accent)');

      const corpo = criar('div', 'persona-opcao-corpo');
      corpo.append(
        criar('span', 'persona-opcao-nome', p.nome),
        criar('span', 'persona-opcao-meta', `${p.customer_age} anos · ${capitalizar(p.customer_occupation)}`),
        criar('span', 'persona-opcao-meta', `${p.cidade}/${p.estado}`),
      );
      const ticket = criar('span', 'persona-opcao-ticket', `Ticket médio ${formatarMoeda(p.perfil_gasto?.ticket_medio)}`);
      corpo.appendChild(ticket);

      const n = contagens[p.id] || 0;
      if (n > 0) corpo.appendChild(criar('span', 'persona-opcao-analisadas', `${n} analisada${n > 1 ? 's' : ''}`));

      btn.append(avatar(p, 'avatar-md'), corpo);
      btn.addEventListener('click', () => onSelect(p.id));
      lista.appendChild(btn);
    });
  }

  // ---- Cartão de perfil -----------------------------------------------

  function renderizarPerfil(p) {
    const el = qs('#cartao-perfil');
    el.innerHTML = '';
    if (!p) { ocultar(el); return; }
    mostrar(el);
    el.style.setProperty('--cor-persona', PERSONAS[p.id]?.cor || 'var(--accent)');

    const topo = criar('div', 'perfil-topo');
    const ident = criar('div', 'perfil-ident');
    ident.append(
      criar('h2', 'perfil-nome', p.nome),
      criar('p', 'perfil-meta', `${p.customer_age} anos · ${capitalizar(p.customer_occupation)}`),
      criar('p', 'perfil-meta', `${p.cidade}/${p.estado}`),
    );
    topo.append(avatar(p, 'avatar-lg'), ident);

    const chips = criar('div', 'chips');
    const desde = p.customer_registration_date ? new Date(p.customer_registration_date).getFullYear() : null;
    [SEGMENTOS[p.customer_segment] || p.customer_segment, RENDAS[p.customer_income_bracket], desde && `Cliente desde ${desde}`]
      .filter(Boolean)
      .forEach(t => chips.appendChild(criar('span', 'chip', t)));

    const bio = criar('p', 'perfil-bio', p.customer_profile || p.perfil_gasto?.descricao || '');

    const pg = p.perfil_gasto || {};
    const stats = criar('dl', 'perfil-stats');
    [
      ['Ticket médio', formatarMoeda(pg.ticket_medio)],
      ['Gasto habitual até', formatarMoeda(pg.limite_habitual_superior)],
      ['Compras por mês', pg.frequencia_mensal ? `~${pg.frequencia_mensal}` : '—'],
      ['Limite do cartão', formatarMoeda(p.card_credit_limit ?? p.customer_total_credit_limit)],
    ].forEach(([rot, val]) => {
      const div = criar('div', 'stat');
      div.append(criar('dt', null, rot), criar('dd', null, val));
      stats.appendChild(div);
    });

    const cartao = criar('div', 'mini-cartao');
    cartao.append(
      criar('span', 'mini-cartao-bandeira', p.card_brand || 'Cartão'),
      criar('span', 'mini-cartao-numero', `•••• ${p.card_last4 || '----'}`),
      criar('span', 'mini-cartao-tipo', capitalizar(p.card_type || '')),
    );

    const blocoCat = criar('div', 'perfil-bloco');
    blocoCat.appendChild(criar('h3', 'perfil-bloco-titulo', 'Costuma comprar em'));
    const cats = criar('div', 'chips');
    (pg.categorias_habituais || []).forEach(c => cats.appendChild(criar('span', 'chip chip-claro', `${emojiCategoria(c)} ${nomeCategoria(c)}`)));
    blocoCat.appendChild(cats);

    const blocoCanal = criar('div', 'perfil-bloco');
    blocoCanal.appendChild(criar('h3', 'perfil-bloco-titulo', 'Canais habituais'));
    const canais = criar('div', 'chips');
    (pg.canais_habituais || []).forEach(c => canais.appendChild(criar('span', 'chip chip-claro', nomeCanal(c))));
    blocoCanal.appendChild(canais);

    el.append(topo, chips, bio, stats, cartao, blocoCat, blocoCanal);
  }

  // ---- Lista de compras -----------------------------------------------

  function renderizarListaCompras(compras, txAtualId, casos, onSelect) {
    const lista = qs('#lista-compras');
    const secao = qs('#secao-compras');
    lista.innerHTML = '';
    mostrar(secao);
    const analisadas = compras.filter(tx => casos[tx.id] && casos[tx.id].estado !== 'novo').length;
    qs('#contador-compras').textContent = `${analisadas} de ${compras.length} analisadas`;

    if (compras.length === 0) {
      lista.appendChild(criar('li', 'msg-vazia', 'Esta persona não tem compras para análise.'));
      return;
    }

    compras.forEach(tx => {
      const li = criar('li', 'item-compra');
      li.setAttribute('role', 'option');
      li.setAttribute('tabindex', '0');
      li.setAttribute('aria-selected', String(tx.id === txAtualId));
      li.dataset.txId = tx.id;

      const cat = tx.estabelecimento?.categoria;
      const ic = criar('span', 'compra-icone', emojiCategoria(cat));
      ic.setAttribute('aria-hidden', 'true');

      const info = criar('div', 'compra-info');
      info.append(
        criar('span', 'compra-estabelecimento', tx.estabelecimento?.nome || '—'),
        criar('span', 'compra-meta', `${nomeCategoria(cat)} · ${formatarData(tx.data_hora)}`),
      );

      const dir = criar('div', 'compra-direita');
      dir.appendChild(criar('span', 'compra-valor', formatarMoeda(tx.valor, tx.moeda)));
      const sit = SITUACAO[casos[tx.id]?.estado];
      if (sit) dir.appendChild(criar('span', `pilula pilula-${sit[1]}`, sit[0]));

      li.append(ic, info, dir);
      const ativar = () => onSelect(tx.id);
      li.addEventListener('click', ativar);
      li.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); ativar(); } });
      lista.appendChild(li);
    });
  }

  // ---- Detalhe da transação -------------------------------------------

  function renderizarTransacao(tx) {
    const card = qs('#cartao-transacao');
    const vazio = qs('#painel-vazio');
    if (!tx) { ocultar(card); mostrar(vazio); return; }
    ocultar(vazio);
    mostrar(card);
    qs('#tx-id').textContent = tx.id;

    const el = qs('#detalhe-transacao');
    el.innerHTML = '';

    const topo = criar('div', 'tx-topo');
    const ic = criar('span', 'tx-icone', emojiCategoria(tx.estabelecimento?.categoria));
    ic.setAttribute('aria-hidden', 'true');
    const nome = criar('div', 'tx-nome');
    nome.append(
      criar('span', 'tx-estabelecimento', tx.estabelecimento?.nome || '—'),
      criar('span', 'tx-categoria', nomeCategoria(tx.estabelecimento?.categoria)),
    );
    const valor = criar('span', 'tx-valor', formatarMoeda(tx.valor, tx.moeda));
    topo.append(ic, nome, valor);

    const local = [tx.localizacao?.cidade, tx.localizacao?.pais].filter(Boolean).join('/');
    const disp = tx.dispositivo?.nome ? `${tx.dispositivo.nome}${tx.dispositivo.novo ? ' (novo)' : ''}` : null;
    const cartaoPresente = tx.card_presence_flag === 'Y' ? 'Sim' : tx.card_presence_flag === 'N' ? 'Não' : null;

    const grade = criar('dl', 'tx-grade');
    [
      ['Data e hora', formatarData(tx.data_hora)],
      ['Canal', nomeCanal(tx.canal)],
      ['Local', local || '—'],
      ['Cartão', tx.cartao ? `${tx.cartao.bandeira} •••• ${tx.cartao.final}` : '—'],
      ['Cartão presente', cartaoPresente],
      ['Dispositivo', disp],
    ].filter(([, v]) => v).forEach(([rot, val]) => {
      const div = criar('div', 'tx-campo');
      div.append(criar('dt', null, rot), criar('dd', null, val));
      grade.appendChild(div);
    });

    el.append(topo, grade);
  }

  /**
   * Compara a compra com o perfil da persona. É só apoio à leitura —
   * a decisão continua sendo do agente de análise.
   */
  function renderizarComparacao(tx, persona) {
    const el = qs('#comparacao-perfil');
    el.innerHTML = '';
    if (!tx || !persona) return;
    const pg = persona.perfil_gasto || {};
    const itens = [];

    const moedaEstrangeira = tx.moeda && tx.moeda !== 'BRL';
    if (moedaEstrangeira) {
      // O perfil está em reais; comparar valores em moedas diferentes enganaria.
      itens.push(['atencao', `Compra em moeda estrangeira (${tx.moeda})`]);
    } else if (pg.ticket_medio && tx.valor !== undefined) {
      const razao = tx.valor / pg.ticket_medio;
      const acima = pg.limite_habitual_superior && tx.valor > pg.limite_habitual_superior;
      const vezes = razao >= 1.5 ? `${razao.toFixed(1).replace('.', ',')}× o ticket médio` : 'perto do ticket médio';
      itens.push(acima
        ? ['atencao', `Valor acima do habitual (${vezes}; costuma gastar até ${formatarMoeda(pg.limite_habitual_superior)})`]
        : ['ok', `Valor dentro do habitual (${razao < 1.5 ? 'perto do ticket médio' : vezes})`]);
    }

    const cat = tx.estabelecimento?.categoria;
    if (cat && pg.categorias_habituais) {
      itens.push(pg.categorias_habituais.includes(cat)
        ? ['ok', `${nomeCategoria(cat)} é uma categoria habitual`]
        : ['atencao', `${nomeCategoria(cat)} não é uma categoria habitual`]);
    }

    if (tx.canal && pg.canais_habituais) {
      itens.push(pg.canais_habituais.includes(tx.canal)
        ? ['ok', `Canal ${nomeCanal(tx.canal).toLowerCase()} é habitual`]
        : ['atencao', `Canal ${nomeCanal(tx.canal).toLowerCase()} não é habitual`]);
    }

    const cidadeTx = tx.localizacao?.cidade;
    if (cidadeTx && persona.cidade) {
      itens.push(cidadeTx === persona.cidade
        ? ['ok', `Compra na cidade do cliente (${cidadeTx})`]
        : ['neutro', `Compra fora da cidade do cliente (${cidadeTx})`]);
    }

    const novoDisp = tx.dispositivo?.novo || (tx.eventos_recentes || []).some(e => e.tipo === 'novo_dispositivo');
    if (novoDisp) itens.push(['atencao', 'Dispositivo novo ou cadastrado recentemente']);

    if (itens.length === 0) return;

    const cab = criar('div', 'comparacao-cabecalho');
    cab.append(
      criar('h3', 'perfil-bloco-titulo', `Comparação com o perfil de ${primeiroNome(persona)}`),
      criar('span', 'texto-suave', 'Calculada pela tela para ajudar a leitura. Quem decide é a IA.'),
    );
    const ul = criar('ul', 'comparacao-lista');
    itens.forEach(([tipo, texto]) => {
      const li = criar('li', `comparacao-item comparacao-${tipo}`);
      li.append(icone(tipo === 'ok' ? 'ok' : tipo === 'atencao' ? 'alerta' : 'info'), criar('span', null, texto));
      ul.appendChild(li);
    });
    el.append(cab, ul);
  }

  // ---- Resultado: veredito, etapas e detalhes -------------------------

  /** Traduz o estado do caso em uma mensagem clara para o usuário. */
  function descreverResultado(caso, persona, opcoes = {}) {
    const nome = primeiroNome(persona);
    const risco = caso.analise?.risco;
    const viaValidacao = risco === 'medio';
    const desfecho = caso.ultima_validacao?.desfecho;

    if (caso.falha_integracao) {
      return {
        tom: 'pendente', icone: 'revisao',
        titulo: 'Execução encerrada sem resultado verificável',
        texto: caso.falha_integracao.mensagem,
        proximo: 'Um analista precisa revisar o caso.',
      };
    }

    if (opcoes.semConexao) {
      return {
        tom: 'pendente', icone: 'alerta',
        titulo: 'A análise não foi executada',
        texto: 'O servidor está sem os IDs dos agentes da IBM (modo offline). Confira as variáveis WXO_* no arquivo .env e reinicie o servidor.',
      };
    }

    switch (caso.estado) {
      case 'analisando':
        return {
          tom: 'processando', icone: 'escudo', carregando: true,
          titulo: 'A IA está analisando a compra…',
          texto: `O agente de análise do watsonx Orchestrate está comparando esta compra com o perfil e o histórico de ${nome}. Isso pode levar alguns segundos.`,
        };
      case 'pendente_execucao':
        return {
          tom: 'processando', icone: 'escudo', carregando: true,
          titulo: 'Análise ainda em andamento na IBM',
          texto: 'A execução remota ainda não terminou. O resultado aparece aqui automaticamente.',
        };
      case 'aguardando_cliente':
        return {
          tom: 'medio', icone: 'chat',
          titulo: `Risco médio: confirmando com ${nome}`,
          texto: `A compra fica retida até ${nome} confirmar se reconhece a compra.`,
          proximo: 'Responda no chat abaixo como se fosse o cliente.',
        };
      case 'validando':
        return {
          tom: 'medio', icone: 'chat', carregando: true,
          titulo: 'Avaliando a resposta do cliente…',
          texto: 'O agente de validação está interpretando a resposta para decidir se libera ou bloqueia.',
        };
      case 'autorizado':
        return {
          tom: 'baixo', icone: 'ok',
          titulo: 'Compra liberada',
          texto: viaValidacao
            ? `${nome} confirmou que reconhece a compra.`
            : `Risco baixo: a compra é compatível com o perfil de ${nome}.`,
          proximo: 'O pagamento segue normalmente.',
        };
      case 'bloqueado':
        return {
          tom: 'alto', icone: 'bloqueio',
          titulo: 'Compra bloqueada',
          texto: viaValidacao
            ? `${nome} não reconheceu a compra.`
            : 'Risco alto: a IA encontrou sinais fortes de fraude.',
          proximo: 'O pagamento foi recusado para proteger o cliente.',
        };
      case 'pendente':
        return {
          tom: 'pendente', icone: 'revisao',
          titulo: 'Encaminhada para revisão humana',
          texto: desfecho === 'pendente' || caso.conversa?.some(t => t.sem_resposta)
            ? `Não foi possível confirmar a compra com ${nome}. Por segurança, ela não foi liberada automaticamente.`
            : 'A IA não chegou a uma conclusão segura. Por segurança, a compra não foi liberada automaticamente.',
          proximo: 'Um analista precisa revisar o caso.',
        };
      default:
        return null;
    }
  }

  function renderizarResultado(caso, persona, opcoes = {}) {
    const card = qs('#cartao-resultado');
    const info = caso && caso.estado !== 'novo' ? descreverResultado(caso, persona, opcoes) : null;
    if (!info) { ocultar(card); return; }
    mostrar(card);

    // Veredito
    const v = qs('#veredito');
    v.innerHTML = '';
    v.className = `veredito veredito-${info.tom}`;
    const selo = criar('div', 'veredito-selo' + (info.carregando ? ' girando' : ''));
    selo.appendChild(icone(info.icone));
    const textos = criar('div', 'veredito-textos');
    textos.append(criar('p', 'veredito-titulo', info.titulo), criar('p', 'veredito-texto', info.texto));
    if (info.proximo) {
      const prox = criar('p', 'veredito-proximo');
      prox.append(criar('strong', null, 'O que acontece agora: '), document.createTextNode(info.proximo));
      textos.appendChild(prox);
    }
    v.append(selo, textos);

    renderizarEtapas(caso, persona);
    renderizarDetalhes(caso);

    toggleOculto(qs('#btn-consultar-run'), caso.estado === 'pendente_execucao');
  }

  function renderizarEtapas(caso, persona) {
    const el = qs('#etapas');
    el.innerHTML = '';
    const risco = caso.analise?.risco;
    const est = caso.estado;
    const nome = primeiroNome(persona);

    const passo1 = risco
      ? { status: 'feito', tom: risco, valor: `Risco ${RISCOS[risco].toLowerCase()}` }
      : { status: 'atual', tom: 'processando', valor: 'Em andamento' };

    let passo2;
    if (!risco) passo2 = { status: 'futuro', valor: 'Depende do risco' };
    else if (risco !== 'medio') passo2 = { status: 'pulado', valor: 'Não foi necessária' };
    else if (est === 'aguardando_cliente' || est === 'validando') passo2 = { status: 'atual', tom: 'medio', valor: `Conversando com ${nome}` };
    else if (est === 'autorizado') passo2 = { status: 'feito', tom: 'baixo', valor: 'Cliente confirmou' };
    else if (est === 'bloqueado') passo2 = { status: 'feito', tom: 'alto', valor: 'Cliente não reconheceu' };
    else passo2 = { status: 'feito', tom: 'pendente', valor: 'Sem confirmação' };

    const finais = {
      autorizado: { status: 'feito', tom: 'baixo', valor: 'Liberada' },
      bloqueado: { status: 'feito', tom: 'alto', valor: 'Bloqueada' },
      pendente: { status: 'feito', tom: 'pendente', valor: 'Revisão humana' },
    };
    const passo3 = finais[est] || { status: 'futuro', valor: 'Aguardando' };

    [['Análise de risco', passo1], ['Validação com o cliente', passo2], ['Decisão final', passo3]]
      .forEach(([titulo, p], i) => {
        const div = criar('div', `etapa etapa-${p.status}` + (p.tom ? ` tom-${p.tom}` : ''));
        const simbolos = { baixo: '✓', medio: '?', alto: '✕', pendente: '•' };
        const simbolo = p.status === 'feito' ? (simbolos[p.tom] || '✓') : p.status === 'pulado' ? '–' : String(i + 1);
        const num = criar('span', 'etapa-num', simbolo);
        num.setAttribute('aria-hidden', 'true');
        const txt = criar('div', 'etapa-textos');
        txt.append(criar('span', 'etapa-titulo', titulo), criar('span', 'etapa-valor', p.valor));
        div.append(num, txt);
        el.appendChild(div);
      });
  }

  function renderizarDetalhes(caso) {
    const el = qs('#detalhe-resultado');
    el.innerHTML = '';
    const a = caso.analise;
    if (!a) return;

    // Medidor de risco
    const medidor = criar('div', 'medidor');
    medidor.setAttribute('role', 'img');
    medidor.setAttribute('aria-label', `Nível de risco: ${RISCOS[a.risco] || a.risco}`);
    [['baixo', 'Libera'], ['medio', 'Valida com o cliente'], ['alto', 'Bloqueia']].forEach(([nivel, acao]) => {
      const seg = criar('div', `medidor-seg seg-${nivel}` + (a.risco === nivel ? ' ativo' : ''));
      seg.append(criar('span', 'medidor-nivel', `Risco ${RISCOS[nivel].toLowerCase()}`), criar('span', 'medidor-acao', acao));
      medidor.appendChild(seg);
    });
    el.appendChild(medidor);

    // Por quê
    if (a.justificativa) {
      const bloco = criar('div', 'bloco-motivo');
      bloco.append(criar('h3', 'perfil-bloco-titulo', 'Por que a IA classificou assim'), criar('p', 'motivo-texto', a.justificativa));
      el.appendChild(bloco);
    }

    // Só mostra a justificativa da validação quando ela bate com a decisão final
    // (ex.: ausência de resposta é convertida em revisão humana pelo backend).
    const val = caso.ultima_validacao;
    const coerente = { autorizar: 'autorizado', bloquear: 'bloqueado', pendente: 'pendente' }[val?.desfecho] === caso.estado;
    if (val && coerente && val.justificativa) {
      const bloco = criar('div', 'bloco-motivo');
      bloco.append(criar('h3', 'perfil-bloco-titulo', 'Conclusão da validação com o cliente'), criar('p', 'motivo-texto', val.justificativa));
      el.appendChild(bloco);
    }

    // Sinais
    const evid = a.evidencias || [];
    if (evid.length) {
      const bloco = criar('div', 'bloco-sinais');
      bloco.appendChild(criar('h3', 'perfil-bloco-titulo', `Sinais que a IA considerou (${evid.length})`));
      const ul = criar('ul', 'lista-sinais');
      evid.forEach(ev => {
        const li = criar('li', 'sinal');
        const cab = criar('div', 'sinal-cab');
        cab.appendChild(criar('span', 'sinal-nome', nomeCampo(ev.caminho)));
        if (ev.valor !== undefined && ev.valor !== null && ev.valor !== '') {
          cab.appendChild(criar('span', 'sinal-valor', formatarValorEvidencia(ev)));
        }
        li.appendChild(cab);
        if (ev.descricao) li.appendChild(criar('p', 'sinal-desc', ev.descricao));
        const cam = criar('code', 'sinal-caminho', ev.caminho);
        cam.title = 'Campo de origem no contexto enviado à IA';
        li.appendChild(cam);
        ul.appendChild(li);
      });
      bloco.appendChild(ul);
      el.appendChild(bloco);
    }

    const lac = a.informacoes_ausentes || [];
    if (lac.length) {
      const bloco = criar('div', 'bloco-lacunas');
      bloco.appendChild(criar('h3', 'perfil-bloco-titulo', 'Informações que a IA não encontrou'));
      const ul = criar('ul', 'lista-lacunas');
      lac.forEach(t => ul.appendChild(criar('li', null, t)));
      bloco.appendChild(ul);
      el.appendChild(bloco);
    }
  }

  // Nome legível para o caminho de uma evidência (ex.: /transacao/amount)
  const CAMPOS = {
    amount: 'Valor da compra', valor: 'Valor da compra',
    acceptor_name: 'Estabelecimento', estabelecimento: 'Estabelecimento', nome: 'Nome',
    acceptor_city: 'Cidade da compra', acceptor_region: 'Estado da compra', acceptor_country_code: 'País da compra',
    localizacao: 'Local da compra', cidade: 'Cidade', pais: 'País/UF',
    mcc: 'Categoria do estabelecimento (MCC)', categoria: 'Categoria',
    canal: 'Canal da compra', card_presence_flag: 'Cartão presente',
    device_id: 'Dispositivo', dispositivo: 'Dispositivo', device_fingerprint: 'Impressão do dispositivo',
    device_ip: 'IP do dispositivo', device_ip_country_code: 'País do IP', novo: 'Dispositivo novo',
    eventos_recentes: 'Eventos recentes', cp_card_term_vel_1h: 'Usos do cartão na última hora',
    cp_card_prev_term_dist_1h: 'Distância do uso anterior (1h)', cardid_count_1m: 'Usos do cartão no último minuto',
    data_hora: 'Horário', event_occurred_at: 'Horário', local_datetime: 'Horário',
    ticket_medio: 'Ticket médio do cliente', limite_habitual_superior: 'Gasto habitual do cliente',
    categorias_habituais: 'Categorias habituais', canais_habituais: 'Canais habituais',
    perfil_gasto: 'Perfil de gastos', historico_resumo: 'Histórico do cliente',
    valor_medio: 'Valor médio no histórico', ticket_medio_historico: 'Ticket médio no histórico',
    categorias_frequentes: 'Categorias frequentes', localizacoes_frequentes: 'Locais frequentes',
    estabelecimentos_frequentes: 'Estabelecimentos frequentes', canais_frequentes: 'Canais frequentes',
    total_transacoes: 'Total de compras no histórico', tipo_transacao: 'Tipo (crédito/débito)',
    customer_city: 'Cidade do cliente', card_credit_limit: 'Limite do cartão', amount_fees: 'Tarifas',
    customer_risk_rating: 'Classificação de risco do cliente',
  };

  function nomeCampo(caminho) {
    const partes = (caminho || '').split('/').filter(p => p && !/^\d+$/.test(p));
    for (let i = partes.length - 1; i >= 0; i--) {
      if (CAMPOS[partes[i]]) return CAMPOS[partes[i]];
    }
    const ultimo = partes[partes.length - 1];
    return ultimo ? capitalizar(ultimo.replace(/_/g, ' ')) : 'Contexto geral';
  }

  function formatarValorEvidencia(ev) {
    const v = ev.valor;
    const chave = (ev.caminho || '').split('/').pop();
    if (typeof v === 'number' && ['amount', 'valor', 'ticket_medio', 'limite_habitual_superior', 'valor_medio', 'ticket_medio_historico', 'card_credit_limit'].includes(chave)) {
      return formatarMoeda(v);
    }
    if (typeof v === 'boolean') return v ? 'Sim' : 'Não';
    if (Array.isArray(v)) return v.map(x => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ');
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }

  // ---- Conversa -------------------------------------------------------

  function renderizarConversa(caso, persona, digitando) {
    const sec = qs('#secao-conversa');
    const conversa = caso?.conversa || [];
    const est = caso?.estado;
    const visivel = conversa.length > 0 || est === 'aguardando_cliente' || est === 'validando';
    toggleOculto(sec, visivel);
    if (!visivel) return;

    const nome = primeiroNome(persona);
    qs('#label-resposta').textContent = `Responder como ${nome}`;
    const perguntas = conversa.filter(t => t.papel !== 'cliente').length;
    qs('#contador-perguntas').textContent = perguntas ? `Pergunta ${perguntas} de até 3` : '';

    const historico = qs('#historico-conversa');
    historico.innerHTML = '';

    conversa.forEach(turno => {
      const ehCliente = turno.papel === 'cliente' || turno.sem_resposta;
      const linha = criar('div', `msg ${ehCliente ? 'msg-cliente' : 'msg-agente'}`);
      const av = ehCliente ? avatar(persona, 'avatar-xs') : criar('span', 'avatar avatar-xs avatar-agente');
      if (!ehCliente) av.appendChild(icone('escudo'));
      const bolha = criar('div', 'bolha' + (turno.sem_resposta ? ' bolha-sem-resposta' : ''));
      bolha.appendChild(criar('span', 'bolha-autor', ehCliente ? nome : 'Assistente Sentinela'));
      bolha.appendChild(criar('span', 'bolha-texto', turno.sem_resposta ? 'Não respondeu' : (turno.texto || '')));
      linha.append(av, bolha);
      historico.appendChild(linha);
    });

    if (digitando) {
      const linha = criar('div', 'msg msg-agente');
      const av = criar('span', 'avatar avatar-xs avatar-agente');
      av.appendChild(icone('escudo'));
      const bolha = criar('div', 'bolha bolha-digitando');
      bolha.setAttribute('aria-label', 'Assistente está digitando');
      bolha.append(criar('i'), criar('i'), criar('i'));
      linha.append(av, bolha);
      historico.appendChild(linha);
    }

    historico.scrollTop = historico.scrollHeight;
  }

  // ---- Resumo da sessão (cabeçalho) -----------------------------------

  function renderizarResumoSessao(casos) {
    const el = qs('#resumo-sessao');
    el.innerHTML = '';
    const lista = Object.values(casos).filter(c => c.estado !== 'novo');
    if (!lista.length) return;
    const cont = { baixo: 0, medio: 0, alto: 0, pendente: 0 };
    lista.forEach(c => {
      const tom = SITUACAO[c.estado]?.[1];
      if (tom in cont) cont[tom]++;
    });
    [['baixo', 'liberada'], ['medio', 'em validação'], ['alto', 'bloqueada'], ['pendente', 'em revisão']]
      .filter(([tom]) => cont[tom] > 0)
      .forEach(([tom, rot]) => {
        const plural = cont[tom] > 1 && rot !== 'em validação' && rot !== 'em revisão' ? rot + 's' : rot;
        el.appendChild(criar('span', `pilula pilula-${tom}`, `${cont[tom]} ${plural}`));
      });
  }

  // ---- Notificações (toast) ------------------------------------------

  function notificar(mensagem, tipo = 'info') {
    const area = qs('#area-notificacoes');
    const toast = criar('div', `notificacao notificacao-${tipo}`, mensagem);
    toast.setAttribute('role', 'alert');
    area.appendChild(toast);
    while (area.children.length > 3) area.firstElementChild.remove();
    setTimeout(() => toast.remove(), 5000);
  }

  // ---- Spinners -------------------------------------------------------

  function mostrarSpinner(btnId) {
    const btn = qs('#' + btnId);
    if (!btn) return;
    btn.disabled = true;
    if (!btn.querySelector('.carregando')) {
      const spin = criar('span', 'carregando');
      spin.setAttribute('aria-hidden', 'true');
      btn.prepend(spin);
    }
  }

  function esconderSpinner(btnId) {
    const btn = qs('#' + btnId);
    if (!btn) return;
    btn.disabled = false;
    const spin = btn.querySelector('.carregando');
    if (spin) spin.remove();
  }

  return {
    qs, qsa, mostrar, ocultar, toggleOculto, criar,
    formatarMoeda, formatarData, primeiroNome,
    renderizarPersonas,
    renderizarPerfil,
    renderizarListaCompras,
    renderizarTransacao,
    renderizarComparacao,
    renderizarResultado,
    renderizarConversa,
    renderizarResumoSessao,
    descreverResultado,
    notificar,
    mostrarSpinner, esconderSpinner,
  };
})();
