from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime
import json
import re
import time
import traceback

ORIGEM = "PVH"
DESTINO = "FOR"
DATA_IDA_ISO = "2026-06-05"
URL = "https://www.maxmilhas.com.br/passagens-aereas"

HEADLESS = True
MAX_TENTATIVAS = 3
TIMEOUT_PADRAO = 30000


def log(msg: str):
    print(f"[LOG] {msg}", flush=True)


def warn(msg: str):
    print(f"[WARN] {msg}", flush=True)


def err(msg: str):
    print(f"[ERRO] {msg}", flush=True)


def salvar_json(dados: dict, arquivo="maxmilhas_resultado.json"):
    with open(arquivo, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    log(f"JSON salvo em: {arquivo}")


def tirar_screenshot(page, nome: str):
    try:
        page.screenshot(path=nome, full_page=True)
        log(f"Screenshot salva: {nome}")
    except Exception as e:
        warn(f"Falha ao salvar screenshot {nome}: {e}")


def salvar_html(page, nome="debug_pagina.html"):
    try:
        html = page.content()
        with open(nome, "w", encoding="utf-8") as f:
            f.write(html)
        log(f"HTML salvo em: {nome}")
    except Exception as e:
        warn(f"Falha ao salvar HTML: {e}")


def normalizar_preco(texto: str):
    m = re.search(r"R\$\s*([\d\.\,]+)", texto)
    if not m:
        return None
    valor = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(valor)
    except ValueError:
        return None


def fechar_popups(page):
    seletores = [
        "button:has-text('Aceitar')",
        "button:has-text('Entendi')",
        "button:has-text('Fechar')",
        "button:has-text('Continuar')",
        "[aria-label='Fechar']",
        "[aria-label='Close']",
    ]
    for seletor in seletores:
        try:
            loc = page.locator(seletor).first
            if loc.is_visible(timeout=1200):
                log(f"Fechando popup: {seletor}")
                loc.click(timeout=3000)
                page.wait_for_timeout(1200)
                return True
        except Exception:
            pass
    return False


def obter_inputs_texto_visiveis(page):
    resultado = []
    inputs = page.locator("input:visible")
    qtd = inputs.count()
    log(f"Inputs visíveis totais: {qtd}")

    for i in range(qtd):
        try:
            inp = inputs.nth(i)
            tipo = (inp.get_attribute("type") or "").lower()
            editable = inp.is_editable()
            placeholder = inp.get_attribute("placeholder")
            aria = inp.get_attribute("aria-label")

            info = {
                "indice_visivel": i,
                "type": tipo,
                "editable": editable,
                "placeholder": placeholder,
                "aria_label": aria,
            }
            log(f"Input visível {i}: {info}")

            if editable and tipo != "checkbox":
                resultado.append((i, inp))
        except Exception as e:
            warn(f"Erro inspecionando input {i}: {e}")

    log(f"Inputs de texto candidatos: {len(resultado)}")
    return resultado


def preencher_input(inp, valor: str, nome: str, page):
    inp.click(timeout=5000)
    page.wait_for_timeout(300)
    try:
        inp.press("Control+A")
        page.wait_for_timeout(100)
        inp.press("Backspace")
    except Exception:
        pass
    page.wait_for_timeout(100)
    inp.fill(valor, timeout=5000)
    page.wait_for_timeout(1200)

    try:
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(250)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1000)
    except Exception:
        pass

    log(f"{nome} preenchido com '{valor}'")


def preencher_campos(page):
    candidatos = obter_inputs_texto_visiveis(page)

    if len(candidatos) < 3:
        return {
            "ok": False,
            "motivo": f"Poucos inputs úteis encontrados: {len(candidatos)}"
        }

    data_br = datetime.strptime(DATA_IDA_ISO, "%Y-%m-%d").strftime("%d/%m/%Y")

    try:
        preencher_input(candidatos[0][1], ORIGEM, "origem", page)
        page.wait_for_timeout(800)

        preencher_input(candidatos[1][1], DESTINO, "destino", page)
        page.wait_for_timeout(800)

        data_ok = False
        for pos in [2, 3]:
            if pos < len(candidatos):
                try:
                    preencher_input(candidatos[pos][1], data_br, "data", page)
                    data_ok = True
                    break
                except Exception as e:
                    warn(f"Falha preenchendo data no candidato {pos}: {e}")

        if not data_ok:
            return {
                "ok": False,
                "motivo": "Não conseguiu preencher a data"
            }

        return {
            "ok": True,
            "motivo": None
        }

    except Exception as e:
        return {
            "ok": False,
            "motivo": f"Falha ao preencher campos: {e}"
        }


def pagina_tem_resultado(page):
    try:
        texto = page.locator("body").inner_text(timeout=10000).lower()

        sinais_resultado = [
            "voos encontrados",
            "resultados",
            "companhia aérea",
            "sem escalas",
            "1 escala",
            "2 escalas",
            "operado por",
            "ordenar",
            "filtrar",
        ]

        url_mudou = page.url != URL
        encontrou_sinal = any(s in texto for s in sinais_resultado)
        encontrou_preco = bool(re.search(r"R\$\s*[\d\.\,]+", texto))

        return url_mudou or (encontrou_sinal and encontrou_preco)
    except Exception:
        return False


def clicar_buscar(page):
    log("Tentando disparar busca...")

    try:
        candidatos = obter_inputs_texto_visiveis(page)
        if candidatos:
            ultimo_input = candidatos[-1][1]
            ultimo_input.click(timeout=3000)
            page.wait_for_timeout(300)
            ultimo_input.press("Enter")
            page.wait_for_timeout(4000)

            if page.url != URL:
                log("Busca disparada com Enter no último input.")
                return True

            if pagina_tem_resultado(page):
                log("Busca disparada com Enter no último input.")
                return True
    except Exception as e:
        warn(f"Falha no Enter do último input: {e}")

    try:
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)

        if page.url != URL or pagina_tem_resultado(page):
            log("Busca disparada com Enter global.")
            return True
    except Exception as e:
        warn(f"Falha no Enter global: {e}")

    seletores = [
        "button[type='submit']",
        "button:has-text('Pesquisar')",
        "button:has-text('Buscar')",
        "[role='button']:has-text('Pesquisar')",
        "[role='button']:has-text('Buscar')",
        "input[type='submit']",
    ]

    for seletor in seletores:
        try:
            loc = page.locator(seletor).first
            if loc.is_visible(timeout=1500):
                log(f"Clicando botão/role: {seletor}")
                loc.click(timeout=5000)
                page.wait_for_timeout(5000)

                if page.url != URL or pagina_tem_resultado(page):
                    return True
        except Exception:
            pass

    textos = ["Pesquisar", "Buscar", "Buscar passagens", "Ver voos"]
    for txt in textos:
        try:
            loc = page.get_by_text(txt, exact=False).first
            if loc.is_visible(timeout=1500):
                log(f"Clicando texto visível: {txt}")
                loc.click(timeout=5000)
                page.wait_for_timeout(5000)

                if page.url != URL or pagina_tem_resultado(page):
                    return True
        except Exception:
            pass

    try:
        houve_submit = page.evaluate("""
        () => {
          const forms = Array.from(document.querySelectorAll('form'));
          if (!forms.length) return false;
          forms[0].requestSubmit ? forms[0].requestSubmit() : forms[0].submit();
          return true;
        }
        """)
        if houve_submit:
            log("Tentado submit via JS no primeiro form.")
            page.wait_for_timeout(5000)

            if page.url != URL or pagina_tem_resultado(page):
                return True
    except Exception as e:
        warn(f"Falha no submit JS: {e}")

    return False


def extrair_precos(page):
    try:
        texto = page.locator("body").inner_text(timeout=10000)
        encontrados = re.findall(r"R\$\s*[\d\.\,]+", texto)
        precos = [normalizar_preco(x) for x in encontrados]
        precos = [p for p in precos if p is not None and p > 200]
        return sorted(set(precos))
    except Exception as e:
        warn(f"Falha ao extrair preços: {e}")
        return []


def criar_browser(p):
    return p.chromium.launch(
        headless=HEADLESS,
        timeout=TIMEOUT_PADRAO,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )


def criar_context(browser):
    return browser.new_context(
        locale="pt-BR",
        timezone_id="America/Porto_Velho",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1366, "height": 768},
    )


def executar_uma_tentativa(tentativa: int):
    browser = None
    context = None

    try:
        log(f"=== Tentativa {tentativa}/{MAX_TENTATIVAS} ===")

        with sync_playwright() as p:
            browser = criar_browser(p)
            context = criar_context(browser)
            page = context.new_page()

            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            log(f"Título: {page.title()}")
            log(f"URL atual: {page.url}")

            fechar_popups(page)
            page.wait_for_timeout(1000)

            tirar_screenshot(page, f"debug_inicio_t{tentativa}.png")

            preenchimento = preencher_campos(page)
            if not preenchimento["ok"]:
                tirar_screenshot(page, f"debug_campos_falha_t{tentativa}.png")
                salvar_html(page, f"debug_campos_falha_t{tentativa}.html")
                return {
                    "ok": False,
                    "motivo": preenchimento["motivo"],
                    "url_final": page.url,
                    "timestamp": datetime.now().isoformat(),
                }

            tirar_screenshot(page, f"debug_campos_t{tentativa}.png")

            buscou = clicar_buscar(page)
            if not buscou:
                tirar_screenshot(page, f"debug_sem_busca_t{tentativa}.png")
                salvar_html(page, f"debug_sem_busca_t{tentativa}.html")
                return {
                    "ok": False,
                    "motivo": "Não encontrou botão de busca",
                    "url_final": page.url,
                    "timestamp": datetime.now().isoformat(),
                }

            page.wait_for_timeout(10000)
            tirar_screenshot(page, f"debug_resultados_t{tentativa}.png")
            salvar_html(page, f"debug_resultados_t{tentativa}.html")

            if not pagina_tem_resultado(page):
                return {
                    "ok": False,
                    "motivo": "Página não aparenta ser de resultados",
                    "url_final": page.url,
                    "timestamp": datetime.now().isoformat(),
                }

            precos = extrair_precos(page)

            return {
                "ok": len(precos) > 0,
                "motivo": None if precos else "Nenhum preço válido encontrado",
                "origem": ORIGEM,
                "destino": DESTINO,
                "data_ida": DATA_IDA_ISO,
                "url_final": page.url,
                "precos_encontrados": precos,
                "menor_preco": min(precos) if precos else None,
                "timestamp": datetime.now().isoformat(),
            }

    except PlaywrightTimeoutError as e:
        return {
            "ok": False,
            "motivo": f"Timeout do Playwright: {e}",
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        traceback.print_exc()
        return {
            "ok": False,
            "motivo": f"Erro inesperado: {e}",
            "timestamp": datetime.now().isoformat(),
        }
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass


def buscar_menor_preco():
    ultimo_resultado = None

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        resultado = executar_uma_tentativa(tentativa)
        ultimo_resultado = resultado

        if resultado.get("ok"):
            salvar_json(resultado)
            print("\n========== RESULTADO ==========")
            print(f"Rota: {resultado['origem']} -> {resultado['destino']}")
            print(f"Data: {resultado['data_ida']}")
            print(f"Menor preço: R$ {resultado['menor_preco']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            print(f"Preços encontrados: {resultado['precos_encontrados']}")
            print(f"URL final: {resultado['url_final']}")
            print("===============================\n")
            return resultado

        warn(f"Tentativa {tentativa} falhou: {resultado.get('motivo', 'Sem detalhe')}")
        if tentativa < MAX_TENTATIVAS:
            time.sleep(3)

    salvar_json(ultimo_resultado or {"ok": False, "motivo": "Sem resultado"})
    print("\nFalhou após todas as tentativas.\n")
    return ultimo_resultado


if __name__ == "__main__":
    buscar_menor_preco()