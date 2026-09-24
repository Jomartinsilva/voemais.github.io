/**
 * RELÉ DE COTAÇÃO — Voe Mais
 * Recebe o pedido de cotação do site e envia para um bot do Telegram NOVO e DEDICADO só a cotações
 * (nunca reutilize o token ou o grupo de outro bot).
 * O token fica SÓ aqui, nos segredos do Cloudflare. Nunca no site, nunca no GitHub.
 * Passo a passo de criação do bot, do grupo e do Worker: PROMPT_CLAUDE_CODE.md, seção "BOT DE COTAÇÃO".
 *
 * Variáveis do Worker (Settings > Variables and Secrets):
 *   TELEGRAM_BOT_TOKEN   (Secret)  token do bot de cotações
 *   TELEGRAM_CHAT_ID     (Secret)  id do grupo privado de cotações
 *   ORIGENS_PERMITIDAS   (Text)    https://jomartinsilva.github.io
 *
 * Defesas: só aceita POST vindo do site (Origin), corpo pequeno, validação campo a campo,
 * campo-isca e tempo mínimo de preenchimento contra robô, limite por IP, texto escapado para o Telegram,
 * e nunca devolve detalhe do Telegram nem o token.
 */

const ORIGENS_PADRAO = 'https://jomartinsilva.github.io';
const LIMITE_CORPO_BYTES = 8192;
const TEMPO_MINIMO_MS = 3000;
const JANELA_MS = 10 * 60 * 1000;
const MAX_POR_JANELA = 5;
const memoriaLimite = new Map();

// Estas listas precisam ser iguais às opções do popup no index.html (há teste automático para isso).
const OPCOES = {
  flex: ['Datas fixas', 'Flexível ± 3 dias', 'Flexível ± 1 semana', 'Flexível no mês'],
  servicos: ['Passagem aérea', 'Hospedagem', 'Seguro viagem', 'Transfer aeroporto/hotel', 'Passeios e ingressos', 'Aluguel de carro'],
  perfil: ['Praia e relax', 'Aventura', 'Cultura e cidade', 'Lua de mel / romântico', 'Em família', 'Luxo', 'Econômico', 'Me proponha! (vocês escolhem por mim)'],
  orcamento: ['', 'Até R$ 3.000', 'R$ 3.000 a R$ 6.000', 'R$ 6.000 a R$ 10.000', 'R$ 10.000 a R$ 20.000', 'Acima de R$ 20.000'],
  hotelCategoria: ['', 'Sem preferência', 'Econômica (até 3 estrelas)', 'Confortável (4 estrelas)', 'Superior (5 estrelas)'],
  hotelRegime: ['', 'Sem preferência', 'Só hospedagem', 'Café da manhã', 'Meia pensão', 'Pensão completa / All inclusive'],
  bagagem: ['', 'Ainda não sei', 'Com bagagem despachada', 'Só bagagem de mão'],
  preferenciaVoo: ['', 'Tanto faz', 'Menor preço', 'Menos escalas / voo direto'],
  passaporte: ['', 'Não sei', 'Todos têm passaporte válido', 'Alguns não têm', 'Ninguém tem'],
  canal: ['WhatsApp', 'E-mail', 'Ligação', 'Mensagem de texto'],
  horario: ['Qualquer horário', 'Manhã', 'Tarde', 'Noite'],
  comoConheceu: ['', 'Instagram', 'Indicação de amigo ou família', 'Google', 'Já sou cliente', 'Outro'],
};

function limpar(valor, max, umaLinha = true) {
  let t = String(valor ?? '').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '');
  t = umaLinha ? t.replace(/\s+/g, ' ') : t.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n');
  return t.trim().slice(0, max);
}

function esc(texto) {
  return String(texto).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function dataValida(iso) {
  if (typeof iso !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return false;
  const d = new Date(iso + 'T00:00:00Z');
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === iso;
}

function inteiro(v, min, max) {
  return Number.isInteger(v) && v >= min && v <= max;
}

/** Valida e normaliza o pedido. Retorna { erro } ou { v }. */
function validar(d) {
  const erro = (m) => ({ erro: m });
  if (!d || typeof d !== 'object' || Array.isArray(d)) return erro('Pedido inválido.');
  if (d.versao !== 1) return erro('Versão do formulário não suportada. Atualize a página.');

  for (const c of ['origem', 'destino', 'nome', 'telefone', 'email']) if (typeof d[c] !== 'string') return erro('Pedido inválido.');
  if (d.obs !== undefined && typeof d.obs !== 'string') return erro('Pedido inválido.');

  const v = {};
  v.origem = limpar(d.origem, 80);
  v.destino = limpar(d.destino, 80);
  if (v.origem.length < 2) return erro('Informe a origem.');
  if (v.destino.length < 2) return erro('Informe o destino.');

  if (!dataValida(d.ida)) return erro('Data de ida inválida.');
  const ontem = new Date(Date.now() - 36 * 3600 * 1000).toISOString().slice(0, 10);
  if (d.ida < ontem) return erro('A data de ida não pode ser no passado.');
  v.ida = d.ida;
  if (typeof d.somenteIda !== 'boolean') return erro('Pedido inválido.');
  v.somenteIda = d.somenteIda;
  if (v.somenteIda) v.volta = null;
  else {
    if (!dataValida(d.volta) || d.volta < d.ida) return erro('Data de volta inválida.');
    v.volta = d.volta;
  }

  for (const campo of Object.keys(OPCOES)) {
    if (campo === 'servicos') continue;
    const valor = d[campo] === undefined ? '' : d[campo];
    if (typeof valor !== 'string' || !OPCOES[campo].includes(valor)) return erro('Uma das opções escolhidas é inválida.');
    v[campo] = valor;
  }
  if (!v.flex || !v.perfil || !v.canal || !v.horario) return erro('Faltam campos obrigatórios.');

  if (!Array.isArray(d.servicos) || d.servicos.length < 1 || d.servicos.length > OPCOES.servicos.length) return erro('Escolha pelo menos um item para cotar.');
  if (!d.servicos.every((s) => typeof s === 'string' && OPCOES.servicos.includes(s)) || new Set(d.servicos).size !== d.servicos.length) return erro('Itens para cotar inválidos.');
  v.servicos = d.servicos;

  if (!inteiro(d.adultos, 1, 9) || !inteiro(d.criancas, 0, 6) || !inteiro(d.idosos, 0, 6) || !inteiro(d.quartos, 1, 5)) return erro('Número de viajantes inválido.');
  if (!Array.isArray(d.idadesCriancas) || d.idadesCriancas.length !== d.criancas || !d.idadesCriancas.every((i) => inteiro(i, 0, 17))) return erro('Idades das crianças inválidas.');
  if (!Array.isArray(d.idadesIdosos) || d.idadesIdosos.length !== d.idosos || !d.idadesIdosos.every((i) => inteiro(i, 60, 100))) return erro('Idades dos viajantes 60+ inválidas.');
  Object.assign(v, { adultos: d.adultos, criancas: d.criancas, idosos: d.idosos, quartos: d.quartos, idadesCriancas: d.idadesCriancas, idadesIdosos: d.idadesIdosos });

  v.obs = limpar(d.obs, 500, false);
  v.nome = limpar(d.nome, 80);
  if (v.nome.length < 3) return erro('Informe seu nome completo.');
  v.telefone = limpar(d.telefone, 20);
  v.telefoneDigitos = v.telefone.replace(/\D/g, '');
  if (v.telefoneDigitos.length < 10 || v.telefoneDigitos.length > 13) return erro('Telefone inválido.');
  v.email = limpar(d.email, 120);
  if (!/^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]{2,}$/.test(v.email)) return erro('E-mail inválido.');
  if (d.consentimento !== true) return erro('É preciso autorizar o uso dos dados.');
  return { v };
}

function dataBR(iso) {
  return iso.split('-').reverse().join('/');
}

function linkWhatsApp(digitos) {
  const numero = digitos.length <= 11 ? '55' + digitos : digitos;
  return 'https://wa.me/' + numero;
}

function protocolo() {
  const sufixo = Math.floor(Math.random() * 1296).toString(36).toUpperCase().padStart(2, '0');
  return 'VM-' + Date.now().toString(36).toUpperCase().slice(-5) + sufixo;
}

function montarMensagem(v, id) {
  const viajantes = [`${v.adultos} ${v.adultos === 1 ? 'adulto' : 'adultos'}`];
  if (v.criancas) viajantes.push(`${v.criancas} ${v.criancas === 1 ? 'criança' : 'crianças'} (${v.idadesCriancas.join(', ')} anos)`);
  if (v.idosos) viajantes.push(`${v.idosos} 60+ (${v.idadesIdosos.join(', ')} anos)`);
  viajantes.push(`${v.quartos} ${v.quartos === 1 ? 'quarto' : 'quartos'}`);

  const l = [];
  l.push(`🧳 <b>Nova cotação ${esc(id)}</b>`, '');
  l.push('<b>Viagem</b>');
  l.push(`Saindo de: ${esc(v.origem)}`);
  l.push(`Destino: ${esc(v.destino)}`);
  l.push(`Ida: ${dataBR(v.ida)}${v.volta ? ` · Volta: ${dataBR(v.volta)}` : ' · Só ida'} (${esc(v.flex)})`);
  l.push(`Cotar: ${v.servicos.map(esc).join(', ')}`, '');
  l.push('<b>Viajantes</b>', esc(viajantes.join(' · ')), '');
  l.push('<b>Preferências</b>', `Perfil: ${esc(v.perfil)}`);
  if (v.orcamento) l.push(`Orçamento: ${esc(v.orcamento)}`);
  if (v.hotelCategoria) l.push(`Hotel: ${esc(v.hotelCategoria)} · Alimentação: ${esc(v.hotelRegime)}`);
  if (v.bagagem) l.push(`Bagagem: ${esc(v.bagagem)} · Voo: ${esc(v.preferenciaVoo)}`);
  if (v.passaporte) l.push(`Passaporte: ${esc(v.passaporte)}`);
  if (v.obs) l.push(`Obs.: ${esc(v.obs)}`);
  l.push('', '<b>Contato</b>', `Nome: ${esc(v.nome)}`);
  l.push(`Telefone: <a href="${linkWhatsApp(v.telefoneDigitos)}">${esc(v.telefone)}</a>`);
  l.push(`E-mail: ${esc(v.email)}`);
  l.push(`Prefere: ${esc(v.canal)} · Horário: ${esc(v.horario)}`);
  if (v.comoConheceu) l.push(`Conheceu por: ${esc(v.comoConheceu)}`);
  const agora = new Date().toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', dateStyle: 'short', timeStyle: 'short' });
  l.push('', `Recebido em ${agora} (Brasília)`);
  return l.join('\n');
}

async function excedeuLimite(request, env) {
  const ip = request.headers.get('CF-Connecting-IP') || 'desconhecido';
  if (env.LIMITADOR && typeof env.LIMITADOR.limit === 'function') {
    const { success } = await env.LIMITADOR.limit({ key: ip });
    return !success;
  }
  const agora = Date.now();
  const recentes = (memoriaLimite.get(ip) || []).filter((t) => agora - t < JANELA_MS);
  if (recentes.length >= MAX_POR_JANELA) { memoriaLimite.set(ip, recentes); return true; }
  recentes.push(agora);
  memoriaLimite.set(ip, recentes);
  if (memoriaLimite.size > 5000) memoriaLimite.clear();
  return false;
}

function responder(status, corpo, origem) {
  const cab = { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' };
  if (origem) Object.assign(cab, { 'Access-Control-Allow-Origin': origem, Vary: 'Origin' });
  return new Response(JSON.stringify(corpo), { status, headers: cab });
}

export default {
  async fetch(request, env) {
    const permitidas = String(env.ORIGENS_PERMITIDAS || ORIGENS_PADRAO).split(',').map((s) => s.trim()).filter(Boolean);
    const origem = request.headers.get('Origin');
    if (!origem || !permitidas.includes(origem)) return responder(403, { ok: false, erro: 'Origem não permitida.' }, null);

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: {
        'Access-Control-Allow-Origin': origem, Vary: 'Origin',
        'Access-Control-Allow-Methods': 'POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Max-Age': '600',
      } });
    }
    if (request.method !== 'POST') return responder(405, { ok: false, erro: 'Método não permitido.' }, origem);
    if (!String(request.headers.get('Content-Type') || '').toLowerCase().startsWith('application/json')) return responder(415, { ok: false, erro: 'Formato não suportado.' }, origem);
    if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return responder(500, { ok: false, erro: 'Serviço temporariamente indisponível.' }, origem);

    const declarado = Number(request.headers.get('Content-Length') || 0);
    if (declarado > LIMITE_CORPO_BYTES) return responder(413, { ok: false, erro: 'Pedido grande demais.' }, origem);
    const texto = await request.text();
    if (new TextEncoder().encode(texto).length > LIMITE_CORPO_BYTES) return responder(413, { ok: false, erro: 'Pedido grande demais.' }, origem);

    let dados;
    try { dados = JSON.parse(texto); } catch { return responder(400, { ok: false, erro: 'Pedido inválido.' }, origem); }

    // Robô: campo-isca preenchido. Responde como se tivesse dado certo, sem enviar nada.
    if (dados && typeof dados === 'object' && typeof dados.site === 'string' && dados.site.trim() !== '') {
      return responder(200, { ok: true, protocolo: 'VM-00000' }, origem);
    }

    if (await excedeuLimite(request, env)) return responder(429, { ok: false, erro: 'Muitas solicitações. Tente de novo em alguns minutos.' }, origem);

    const r = validar(dados);
    if (r.erro) return responder(422, { ok: false, erro: r.erro }, origem);
    if (!Number.isFinite(dados.tempoMs) || dados.tempoMs < TEMPO_MINIMO_MS) return responder(422, { ok: false, erro: 'Preenchimento rápido demais. Confira os dados e envie de novo.' }, origem);

    const id = protocolo();
    try {
      const resp = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text: montarMensagem(r.v, id), parse_mode: 'HTML', disable_web_page_preview: true }),
      });
      const retorno = await resp.json().catch(() => null);
      if (!resp.ok || !retorno || retorno.ok !== true) throw new Error('telegram');
    } catch {
      return responder(502, { ok: false, erro: 'Não conseguimos registrar o pedido agora.' }, origem);
    }
    return responder(200, { ok: true, protocolo: id }, origem);
  },
};
