"use strict";

const API = "/api";

const estado = {
  sessao: null,
  dashboard: null,
  equipamentos: [],
  editandoId: null,
  confirmarResolve: null,
};

const $ = (seletor) => document.querySelector(seletor);
const $$ = (seletor) => Array.from(document.querySelectorAll(seletor));

function escapar(valor) {
  return String(valor ?? "").replace(/[&<>"']/g, (caractere) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[caractere]);
}

function slug(valor) {
  return String(valor ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function tomStatus(nome) {
  const texto = slug(nome);
  if (texto === "funcionando") return "success";
  if (texto.includes("defeito") || texto.includes("nao-liga")) return "danger";
  if (texto.includes("reparo")) return "warning";
  return "muted";
}

function tomEstado(nome) {
  const texto = slug(nome);
  if (texto === "bom") return "success";
  if (texto === "regular") return "info";
  if (texto === "ruim" || texto === "danificado" || texto === "nao-localizado") return "danger";
  return "muted";
}

function numero(valor) {
  return new Intl.NumberFormat("pt-BR").format(valor || 0);
}

function percentual(parte, total) {
  if (!total) return "0%";
  return `${Math.round((parte / total) * 100)}%`;
}

function dataHora(valor) {
  if (!valor) return "-";
  const partes = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(String(valor));
  if (!partes) return String(valor);
  const data = `${partes[3]}/${partes[2]}/${partes[1]}`;
  return partes[4] ? `${data} ${partes[4]}:${partes[5]}` : data;
}

function opcoesJson(metodo, corpo) {
  return {
    method: metodo,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corpo),
  };
}

async function api(caminho, opcoes = {}) {
  const resposta = await fetch(API + caminho, { credentials: "same-origin", ...opcoes });
  let dados = null;
  try {
    dados = await resposta.json();
  } catch (erro) {
    dados = null;
  }
  if (!resposta.ok) {
    throw new Error((dados && dados.erro) || `Erro ${resposta.status} na requisição.`);
  }
  return dados;
}

function mostrarToast(mensagem, tipo = "info") {
  const toast = document.createElement("div");
  toast.className = `toast ${tipo}`;
  toast.textContent = mensagem;
  $("#toast-stack").appendChild(toast);
  window.setTimeout(() => toast.remove(), 4500);
}

function alternarView(nome) {
  $$(".view").forEach((secao) => secao.classList.toggle("active", secao.id === `view-${nome}`));
  $$(".nav-item").forEach((botao) => botao.classList.toggle("active", botao.dataset.view === nome));
}

function abrirModal(id) {
  const modal = $(`#modal-${id}`);
  if (!modal) return;
  modal.hidden = false;
  const campo = modal.querySelector("input:not([type=hidden])");
  if (campo) campo.focus();
}

function fecharModal(id) {
  const modal = $(`#modal-${id}`);
  if (modal) modal.hidden = true;
}

function pedirConfirmacao(titulo, mensagem) {
  return new Promise((resolve) => {
    $("#confirmar-titulo").textContent = titulo;
    $("#confirmar-mensagem").textContent = mensagem;
    estado.confirmarResolve = resolve;
    $("#modal-confirm").hidden = false;
    $("#btn-confirmar-ok").focus();
  });
}

function resolverConfirmacao(valor) {
  const resolve = estado.confirmarResolve;
  estado.confirmarResolve = null;
  $("#modal-confirm").hidden = true;
  if (resolve) resolve(valor);
}

function renderizarBarras(seletor, itens, tipo) {
  const alvo = $(seletor);
  if (!itens || !itens.length) {
    alvo.innerHTML = '<p class="panel-note">Sem dados para exibir.</p>';
    return;
  }
  const maxima = Math.max(...itens.map((item) => item.quantidade), 1);
  alvo.innerHTML = itens.map((item) => {
    const tom = tipo === "status" ? tomStatus(item.nome) : tomEstado(item.nome);
    const largura = Math.max(3, Math.round((item.quantidade / maxima) * 100));
    return `
      <div class="bar-row">
        <span class="bar-label" title="${escapar(item.nome)}">${escapar(item.nome || "Não informado")}</span>
        <div class="bar-track"><div class="bar-fill tom-${tom}" style="width:${largura}%"></div></div>
        <span class="bar-value">${numero(item.quantidade)}</span>
      </div>`;
  }).join("");
}

function renderizarDashboard() {
  const dados = estado.dashboard;
  if (!dados) return;

  const cartoes = [
    { rotulo: "Total de equipamentos", valor: dados.total, detalhe: `${numero(dados.porLocal.length)} localizações catalogadas`, tom: "info" },
    { rotulo: "Funcionando", valor: dados.funcionando, detalhe: `${percentual(dados.funcionando, dados.total)} do inventário`, tom: "success" },
    { rotulo: "Não funcionando", valor: dados.nao_funcionando, detalhe: "Com defeito, não liga ou em reparo", tom: "danger" },
    { rotulo: "Disponíveis para uso", valor: dados.disponiveis, detalhe: "Funcionando, com carregador e sem dano", tom: "success" },
    { rotulo: "Em manutenção", valor: dados.manutencao, detalhe: "Aguardando reparo", tom: "warning" },
    { rotulo: "Sem informação", valor: dados.sem_informacao, detalhe: "Status não preenchido na planilha", tom: "muted" },
  ];

  $("#kpi-grid").innerHTML = cartoes.map((cartao) => `
    <article class="kpi-card tom-${cartao.tom}">
      <div class="kpi-label">${escapar(cartao.rotulo)}</div>
      <div class="kpi-value">${numero(cartao.valor)}</div>
      <div class="kpi-detail">${escapar(cartao.detalhe)}</div>
    </article>`).join("");

  renderizarBarras("#barras-status", dados.porStatus, "status");
  renderizarBarras("#barras-estado", dados.porEstado, "estado");

  if (!dados.porLocal.length) {
    $("#corpo-locais").innerHTML = '<tr class="empty-row"><td colspan="7">Nenhum equipamento importado.</td></tr>';
  } else {
    $("#corpo-locais").innerHTML = dados.porLocal.map((local) => {
      const participacao = Math.round((local.total / Math.max(dados.total, 1)) * 100);
      return `
        <tr>
          <td class="cell-strong">${escapar(local.nome || "Não informado")}</td>
          <td class="num">${numero(local.total)}</td>
          <td class="num">${numero(local.funcionando)}</td>
          <td class="num">${numero(local.disponiveis)}</td>
          <td class="num">${numero(local.indisponiveis)}</td>
          <td class="num">${numero(local.sem_informacao)}</td>
          <td class="participation">${participacao}%
            <div class="mini-bar"><span style="width:${participacao}%"></span></div>
          </td>
        </tr>`;
    }).join("");
  }

  const metadados = dados.metadados || {};
  $("#fonte-dados").textContent = metadados.planilha_origem || "Banco local";
  $("#ultima-importacao").textContent = dataHora(metadados.ultima_importacao);
  $("#atualizado-em").textContent = `Atualizado em ${dataHora(dados.geradoEm)}`;
  renderizarAdmin();
}

function preencherSeletor(seletor, mensagem, valores) {
  const atual = seletor.value;
  seletor.innerHTML = `<option value="">${escapar(mensagem)}</option>` +
    valores.map((valor) => `<option value="${escapar(valor)}">${escapar(valor)}</option>`).join("");
  seletor.value = valores.includes(atual) ? atual : "";
}

function preencherFiltros() {
  const locais = Array.from(new Set(estado.equipamentos.map((item) => item.localizacao).filter(Boolean))).sort();
  const origens = Array.from(new Set(estado.equipamentos.map((item) => item.origem).filter(Boolean))).sort();
  const status = Array.from(new Set(estado.equipamentos.map((item) => item.status).filter(Boolean))).sort();
  preencherSeletor($("#filtro-local"), "Todas as localizações", locais);
  preencherSeletor($("#filtro-origem"), "Todas as origens", origens);
  preencherSeletor($("#filtro-status"), "Todos os status", status);
}

function renderizarEquipamentos() {
  const busca = $("#filtro-busca")?.value.toLocaleLowerCase("pt-BR").trim() || "";
  const status = $("#filtro-status")?.value || "";
  const local = $("#filtro-local")?.value || "";
  const origem = $("#filtro-origem")?.value || "";

  const filtrados = estado.equipamentos.filter((item) => {
    if (status && item.status !== status) return false;
    if (local && item.localizacao !== local) return false;
    if (origem && item.origem !== origem) return false;
    if (!busca) return true;
    return [item.etiqueta, item.numero_serie, item.marca, item.modelo, item.localizacao, item.observacoes]
      .some((campo) => String(campo || "").toLocaleLowerCase("pt-BR").includes(busca));
  });

  $("#contador-equipamentos").textContent = `${numero(filtrados.length)} de ${numero(estado.equipamentos.length)} equipamentos`;
  const corpo = $("#corpo-equipamentos");

  if (!filtrados.length) {
    corpo.innerHTML = '<tr class="empty-row"><td colspan="8">Nenhum equipamento corresponde aos filtros selecionados.</td></tr>';
    return;
  }

  const autenticado = Boolean(estado.sessao && estado.sessao.autenticado);
  const administrador = autenticado && estado.sessao.usuario.perfil === "admin";

  corpo.innerHTML = filtrados.map((item) => {
    const equipamento = [item.marca, item.modelo].filter(Boolean).join(" ");
    const titulo = item.observacoes ? ` title="${escapar(item.observacoes)}"` : "";
    const botoes = [];
    if (autenticado) {
      botoes.push(`<button class="btn btn-quiet mini" type="button" data-acao="editar" data-id="${item.id}">Editar</button>`);
    }
    if (administrador) {
      botoes.push(`<button class="btn btn-quiet mini" type="button" data-acao="excluir" data-id="${item.id}">Excluir</button>`);
    }
    return `
      <tr>
        <td>
          <span class="cell-strong">${escapar(item.etiqueta || "Sem etiqueta")}</span>
          <span class="cell-secondary">${escapar(item.origem || "-")}</span>
        </td>
        <td><span class="cell-mono">${escapar(item.numero_serie || "-")}</span></td>
        <td>${escapar(item.localizacao || "-")}</td>
        <td${titulo}>
          <span class="cell-strong">${escapar(equipamento || "Não informado")}</span>
          ${item.responsavel_levantamento ? `<span class="cell-secondary">Resp.: ${escapar(item.responsavel_levantamento)}</span>` : ""}
        </td>
        <td><span class="pill tom-${tomEstado(item.estado_fisico)}">${escapar(item.estado_fisico)}</span></td>
        <td><span class="pill tom-${tomStatus(item.status)}">${escapar(item.status)}</span></td>
        <td>${escapar(item.carregador || "-")}</td>
        <td>${botoes.length ? `<div class="row-actions">${botoes.join("")}</div>` : "-"}</td>
      </tr>`;
  }).join("");
}

function renderizarAdmin() {
  const perfil = estado.sessao && estado.sessao.autenticado ? estado.sessao.usuario.perfil : "";
  const metadados = (estado.dashboard && estado.dashboard.metadados) || {};
  $("#admin-total").textContent = numero(estado.dashboard ? estado.dashboard.total : 0);
  $("#admin-origem").textContent = metadados.planilha_origem || "-";
  $("#admin-perfil").textContent = perfil === "admin" ? "Administrador" : perfil === "professor" ? "Professor" : "-";
}

function aplicarSessao() {
  const autenticado = Boolean(estado.sessao && estado.sessao.autenticado);
  const administrador = autenticado && estado.sessao.usuario.perfil === "admin";

  $("#mode-badge").textContent = autenticado ? "Modo edição" : "Consulta pública";
  $("#mode-badge").classList.toggle("editing", autenticado);
  $("#user-area").hidden = !autenticado;
  $("#btn-abrir-login").hidden = autenticado;
  $("#nav-gestao").hidden = !autenticado;
  $("#gestao-bloqueado").hidden = autenticado;
  $("#gestao-liberado").hidden = !autenticado;
  $("#acoes-administrador").hidden = !administrador;
  $("#admin-warning").hidden = administrador;

  if (autenticado) {
    $("#user-name").textContent = estado.sessao.usuario.nome;
  } else {
    limparFormulario();
  }

  renderizarAdmin();
  renderizarEquipamentos();
}

function limparFormulario() {
  estado.editandoId = null;
  const formulario = $("#form-equipamento");
  if (formulario) formulario.reset();
  $("#titulo-formulario").textContent = "Novo equipamento";
  $("#form-hint").textContent = "Preencha os dados e salve para atualizar o inventário.";
  $("#btn-salvar").textContent = "Salvar no banco";
}

function preencherFormulario(item) {
  estado.editandoId = item.id;
  const formulario = $("#form-equipamento");
  const campos = [
    "etiqueta", "numero_serie", "localizacao", "marca", "modelo", "estado_fisico",
    "status", "carregador", "data_levantamento", "responsavel_levantamento", "observacoes",
  ];
  campos.forEach((campo) => {
    const elemento = formulario.elements.namedItem(campo);
    if (elemento) elemento.value = item[campo] || "";
  });
  $("#titulo-formulario").textContent = `Editar ${item.etiqueta || "equipamento"}`;
  $("#form-hint").textContent = `Registro ${item.id}. As alterações serão gravadas no banco de dados.`;
  $("#btn-salvar").textContent = "Salvar alterações";
  alternarView("gestao");
  formulario.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function carregarSessao() {
  estado.sessao = await api("/sessao");
  aplicarSessao();
}

async function carregarDados() {
  const [dashboard, lista] = await Promise.all([api("/dashboard"), api("/equipamentos")]);
  estado.dashboard = dashboard;
  estado.equipamentos = lista.itens;
  renderizarDashboard();
  preencherFiltros();
  renderizarEquipamentos();
}

async function entrar(evento) {
  evento.preventDefault();
  const dados = Object.fromEntries(new FormData(evento.target));
  const botao = evento.target.querySelector('button[type="submit"]');
  $("#login-erro").hidden = true;
  botao.disabled = true;

  try {
    await api("/login", opcoesJson("POST", dados));
    await carregarSessao();
    await carregarDados();
    evento.target.reset();
    fecharModal("login");
    mostrarToast("Acesso liberado para a gestão do inventário.", "success");
    alternarView("gestao");
  } catch (erro) {
    $("#login-erro").textContent = erro.message;
    $("#login-erro").hidden = false;
  } finally {
    botao.disabled = false;
  }
}

async function sair() {
  try {
    await api("/logout", { method: "POST" });
  } catch (erro) {
    mostrarToast(erro.message, "error");
  }
  estado.sessao = { autenticado: false, usuario: null };
  aplicarSessao();
  alternarView("visao-geral");
  mostrarToast("Sessão encerrada.");
}

async function salvarFormulario() {
  const formulario = $("#form-equipamento");
  if (!formulario.reportValidity()) return;
  const dados = Object.fromEntries(new FormData(formulario));
  const identificador = estado.editandoId;
  const botao = $("#btn-salvar");
  botao.disabled = true;

  try {
    await api(
      identificador ? `/equipamentos/${identificador}` : "/equipamentos",
      opcoesJson(identificador ? "PUT" : "POST", dados),
    );
    await carregarDados();
    limparFormulario();
    mostrarToast(identificador ? "Equipamento atualizado." : "Equipamento cadastrado.", "success");
  } catch (erro) {
    mostrarToast(erro.message, "error");
  } finally {
    botao.disabled = false;
  }
}

async function excluirEquipamento(identificador) {
  const item = estado.equipamentos.find((registro) => registro.id === identificador);
  if (!item) return;
  const confirmado = await pedirConfirmacao(
    "Excluir equipamento",
    `O registro ${item.etiqueta || "sem etiqueta"} será removido do banco de dados. Esta ação não pode ser desfeita.`,
  );
  if (!confirmado) return;

  try {
    await api(`/equipamentos/${identificador}`, { method: "DELETE" });
    await carregarDados();
    mostrarToast("Equipamento removido.", "success");
  } catch (erro) {
    mostrarToast(erro.message, "error");
  }
}

async function reimportarPlanilha() {
  const confirmado = await pedirConfirmacao(
    "Reimportar planilha",
    "Os registros atuais do banco serão substituídos pelos dados da planilha de inventário. Alterações manuais serão perdidas.",
  );
  if (!confirmado) return;

  try {
    const resposta = await api("/importar", { method: "POST" });
    await carregarDados();
    mostrarToast(`${numero(resposta.resultado.total)} registros importados da planilha.`, "success");
  } catch (erro) {
    mostrarToast(erro.message, "error");
  }
}

function vincularEventos() {
  $$(".nav-item").forEach((botao) => {
    botao.addEventListener("click", () => alternarView(botao.dataset.view));
  });

  ["#btn-abrir-login", "#btn-abrir-login-gestao"].forEach((seletor) => {
    $(seletor).addEventListener("click", () => abrirModal("login"));
  });

  $$("[data-fechar]").forEach((elemento) => {
    elemento.addEventListener("click", () => {
      if (elemento.dataset.fechar === "confirm") resolverConfirmacao(false);
      else fecharModal(elemento.dataset.fechar);
    });
  });

  $("#form-login").addEventListener("submit", entrar);
  $("#btn-sair").addEventListener("click", sair);
  $("#btn-novo").addEventListener("click", limparFormulario);
  $("#btn-salvar").addEventListener("click", salvarFormulario);
  $("#btn-importar").addEventListener("click", reimportarPlanilha);

  $("#btn-confirmar-cancelar").addEventListener("click", () => resolverConfirmacao(false));
  $("#btn-confirmar-ok").addEventListener("click", () => resolverConfirmacao(true));

  ["#filtro-busca", "#filtro-status", "#filtro-local", "#filtro-origem"].forEach((seletor) => {
    $(seletor).addEventListener("input", renderizarEquipamentos);
    $(seletor).addEventListener("change", renderizarEquipamentos);
  });

  $("#btn-limpar-filtros").addEventListener("click", () => {
    ["#filtro-busca", "#filtro-status", "#filtro-local", "#filtro-origem"].forEach((seletor) => {
      $(seletor).value = "";
    });
    renderizarEquipamentos();
  });

  $("#corpo-equipamentos").addEventListener("click", (evento) => {
    const botao = evento.target.closest("button[data-acao]");
    if (!botao) return;
    const identificador = Number(botao.dataset.id);
    if (botao.dataset.acao === "editar") {
      const item = estado.equipamentos.find((registro) => registro.id === identificador);
      if (item) preencherFormulario(item);
    } else if (botao.dataset.acao === "excluir") {
      excluirEquipamento(identificador);
    }
  });

  document.addEventListener("keydown", (evento) => {
    if (evento.key !== "Escape") return;
    if (!$("#modal-confirm").hidden) resolverConfirmacao(false);
    else fecharModal("login");
  });
}

document.addEventListener("DOMContentLoaded", () => {
  vincularEventos();
  Promise.all([carregarSessao(), carregarDados()])
    .catch((erro) => mostrarToast(erro.message, "error"));
});
