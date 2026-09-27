(() => {
  "use strict";

  const body = document.body;
  const openButton = document.querySelector("[data-sidebar-open]");
  const closeTarget = document.querySelector("[data-sidebar-close]");

  const setSidebar = (open) => {
    body.classList.toggle("sidebar-open", open);
    if (openButton) openButton.setAttribute("aria-expanded", String(open));
  };

  openButton?.addEventListener("click", () => setSidebar(true));
  closeTarget?.addEventListener("click", () => setSidebar(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setSidebar(false);
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelector("[data-print-ticket]")?.addEventListener("click", () => {
    window.print();
  });

  const checkout = document.querySelector("[data-checkout]");
  const codeInput = document.querySelector("[data-code-input]");

  const focusCodeInput = () => {
    if (codeInput instanceof HTMLInputElement) {
      codeInput.focus();
      codeInput.select();
    }
  };

  if (checkout instanceof HTMLElement) {
    body.classList.add("checkout-enhanced");
    const panels = [...checkout.querySelectorAll("[data-checkout-panel]")];
    const stepButtons = [...checkout.querySelectorAll("[data-checkout-go]")];
    const allowedSteps = new Set(["produtos", "pagamento"]);

    const showStep = (requestedStep, moveViewport = true) => {
      const step = allowedSteps.has(requestedStep) ? requestedStep : "produtos";
      const target = panels.find((panel) => panel.dataset.checkoutPanel === step);
      if (!(target instanceof HTMLElement)) return;

      panels.forEach((panel) => {
        const active = panel === target;
        panel.classList.toggle("is-active", active);
        panel.setAttribute("aria-hidden", String(!active));
      });
      stepButtons.forEach((button) => {
        const active = button.dataset.checkoutGo === step;
        button.classList.toggle("is-active", active);
        if (active) button.setAttribute("aria-current", "step");
        else button.removeAttribute("aria-current");
      });

      if (moveViewport) checkout.scrollIntoView({ behavior: "smooth", block: "start" });
      if (step === "produtos") focusCodeInput();
    };

    stepButtons.forEach((button) => {
      button.addEventListener("click", () => {
        if (!button.disabled) showStep(button.dataset.checkoutGo);
      });
    });

    const paymentForm = checkout.querySelector("[data-payment-form]");
    if (paymentForm instanceof HTMLFormElement) {
      const paymentButtons = [...paymentForm.querySelectorAll("[data-payment-method]")];
      const paymentFields = [...paymentForm.querySelectorAll("[data-payment-field]")];
      const paymentInputs = [...paymentForm.querySelectorAll("[data-payment-input]")];
      const paymentIntro = paymentForm.querySelector("[data-payment-intro]");
      const paymentConfirmation = paymentForm.querySelector("[data-payment-confirmation]");
      const paymentLabel = paymentForm.querySelector("[data-payment-label]");
      const paymentSubmit = paymentForm.querySelector("[data-payment-submit]");
      const changePreview = paymentForm.querySelector("[data-change-preview]");
      const confirmationInput = paymentForm.querySelector("input[type='checkbox']");
      const rawTotal = checkout.dataset.saleTotal || "0";
      const total = Number(rawTotal.replace(",", "."));
      const formattedTotal = total.toFixed(2).replace(".", ",");
      const labels = {
        dinheiro: "Dinheiro",
        pix: "PIX",
        debito: "Cartão de débito",
        credito: "Cartão de crédito",
        misto: "Pagamento misto",
      };

      const inputFor = (name) => paymentInputs.find((input) => input.dataset.paymentInput === name);
      const setVisibleFields = (names) => {
        paymentFields.forEach((field) => {
          field.classList.toggle("is-visible", names.has(field.dataset.paymentField));
        });
      };
      const clearPayments = () => {
        paymentInputs.forEach((input) => {
          input.value = "";
          input.readOnly = false;
        });
        if (confirmationInput instanceof HTMLInputElement) confirmationInput.checked = false;
      };

      const selectPayment = (method) => {
        if (!(method in labels)) return;
        clearPayments();
        paymentButtons.forEach((button) => {
          const selected = button.dataset.paymentMethod === method;
          button.classList.toggle("is-selected", selected);
          button.setAttribute("aria-pressed", String(selected));
        });
        paymentIntro?.classList.add("is-hidden");
        paymentConfirmation?.classList.add("is-visible");
        if (paymentLabel) paymentLabel.textContent = labels[method];
        if (paymentSubmit instanceof HTMLInputElement) paymentSubmit.disabled = false;

        if (method === "misto") {
          setVisibleFields(new Set(["dinheiro", "recebido", "pix", "debito", "credito"]));
          inputFor("dinheiro")?.focus();
          return;
        }

        const visible = new Set([method]);
        if (method === "dinheiro") visible.add("recebido");
        setVisibleFields(visible);
        const selectedInput = inputFor(method);
        if (selectedInput instanceof HTMLInputElement) {
          selectedInput.value = formattedTotal;
          selectedInput.readOnly = true;
        }
        if (method === "dinheiro") inputFor("recebido")?.focus();
        else if (confirmationInput instanceof HTMLInputElement) confirmationInput.focus();
      };

      paymentButtons.forEach((button) => {
        button.addEventListener("click", () => selectPayment(button.dataset.paymentMethod));
      });
      setVisibleFields(new Set());
      paymentConfirmation?.classList.remove("is-visible");
      if (paymentSubmit instanceof HTMLInputElement) paymentSubmit.disabled = true;

      const receivedInput = inputFor("recebido");
      receivedInput?.addEventListener("input", () => {
        if (!changePreview) return;
        const received = Number(receivedInput.value.replace(",", "."));
        if (Number.isFinite(received) && received >= total) {
          changePreview.textContent = `Troco previsto: ${(received - total).toLocaleString("pt-BR", { style: "currency", currency: "BRL" })}`;
        } else {
          changePreview.textContent = "O troco será validado pelo servidor.";
        }
      });
    }

    showStep(checkout.dataset.initialStep || "produtos", false);
  } else {
    focusCodeInput();
  }
})();

(() => {
  "use strict";

  const clock = document.querySelector("[data-live-clock]");
  const dateTarget = document.querySelector("[data-live-date]");
  const timeTarget = document.querySelector("[data-live-time]");

  if (clock instanceof HTMLElement && dateTarget && timeTarget) {
    const updateClock = () => {
      const now = new Date();
      dateTarget.textContent = new Intl.DateTimeFormat("pt-BR", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      }).format(now);
      timeTarget.textContent = new Intl.DateTimeFormat("pt-BR", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(now);
    };
    updateClock();
    window.setInterval(updateClock, 15000);
  }

  const saleList = document.querySelector("[data-auto-scroll-list]");
  if (saleList instanceof HTMLElement) {
    const scrollToLatest = () => {
      saleList.scrollTop = saleList.scrollHeight;
    };
    window.requestAnimationFrame(scrollToLatest);
    window.setTimeout(scrollToLatest, 80);
  }

  const chargeButton = document.querySelector('[data-checkout-go="pagamento"]');
  const discardForm = document.querySelector("form[data-discard-sale]");

  document.addEventListener("keydown", (event) => {
    if (event.key === "F10" && chargeButton instanceof HTMLButtonElement && !chargeButton.disabled) {
      event.preventDefault();
      chargeButton.click();
      return;
    }

    if (event.key === "F8" && discardForm instanceof HTMLFormElement) {
      event.preventDefault();
      discardForm.requestSubmit();
    }
  });
})();

(() => {
  "use strict";
  const operatorLock = document.querySelector("[data-operator-lock][data-operator-cash-url]");
  if (operatorLock instanceof HTMLElement && operatorLock.dataset.operatorCashUrl) {
    window.setTimeout(() => window.location.replace(operatorLock.dataset.operatorCashUrl), 120);
  }
})();

(() => {
  "use strict";

  const openHashTarget = () => {
    if (!window.location.hash) return;
    const target = document.querySelector(window.location.hash);
    if (!(target instanceof HTMLElement)) return;
    if (target instanceof HTMLDetailsElement) target.open = true;
    window.requestAnimationFrame(() => target.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  window.addEventListener("DOMContentLoaded", openHashTarget);
  window.addEventListener("hashchange", openHashTarget);
})();
