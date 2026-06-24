(function () {
  const EVENT_MAP = {
    click: 'data-ichat-onclick',
    input: 'data-ichat-oninput',
    change: 'data-ichat-onchange',
    submit: 'data-ichat-onsubmit'
  };

  function splitStatements(expression) {
    const statements = [];
    let quote = null;
    let depth = 0;
    let start = 0;
    for (let i = 0; i < expression.length; i += 1) {
      const char = expression[i];
      const prev = expression[i - 1];
      if ((char === '"' || char === "'") && prev !== '\\') {
        quote = quote === char ? null : (quote || char);
      } else if (!quote && char === '(') {
        depth += 1;
      } else if (!quote && char === ')') {
        depth -= 1;
      } else if (!quote && depth === 0 && char === ';') {
        statements.push(expression.slice(start, i).trim());
        start = i + 1;
      }
    }
    const tail = expression.slice(start).trim();
    if (tail) statements.push(tail);
    return statements;
  }

  function splitArgs(argsText) {
    if (!argsText.trim()) return [];
    const args = [];
    let quote = null;
    let start = 0;
    for (let i = 0; i < argsText.length; i += 1) {
      const char = argsText[i];
      const prev = argsText[i - 1];
      if ((char === '"' || char === "'") && prev !== '\\') {
        quote = quote === char ? null : (quote || char);
      } else if (!quote && char === ',') {
        args.push(argsText.slice(start, i).trim());
        start = i + 1;
      }
    }
    args.push(argsText.slice(start).trim());
    return args;
  }

  function parseArg(raw, event, target) {
    const value = raw.trim();
    if (value === 'event') return event;
    if (value === 'this') return target;
    if (value === 'this.value') return target.value;
    if (value === 'true') return true;
    if (value === 'false') return false;
    if (value === 'null') return null;
    if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
    const ternaryMatch = value.match(/^([A-Za-z_$][\w$]*)\s*===\s*(['"])([\s\S]*?)\2\s*\?\s*(['"])([\s\S]*?)\4\s*:\s*(['"])([\s\S]*?)\6$/);
    if (ternaryMatch) {
      return window[ternaryMatch[1]] === ternaryMatch[3] ? ternaryMatch[5] : ternaryMatch[7];
    }
    const datasetMatch = value.match(/^this\.dataset\.([A-Za-z0-9_]+)$/);
    if (datasetMatch) return target.dataset[datasetMatch[1]];
    const stringMatch = value.match(/^(['"])([\s\S]*)\1$/);
    if (stringMatch) return stringMatch[2].replace(/\\'/g, "'").replace(/\\"/g, '"');
    if (/^[A-Za-z_$][\w$]*$/.test(value)) return window[value];
    return value;
  }

  function callNamedFunction(statement, event, target) {
    let text = statement.trim();
    let shouldPreventDefault = false;
    if (text.startsWith('return ')) {
      shouldPreventDefault = true;
      text = text.slice(7).trim();
    }
    if (text === 'return false') return false;
    if (text === 'event.stopPropagation()') {
      event.stopPropagation();
      return true;
    }
    const ifMatch = text.match(/^if\(([^)]+)\)\s+([\s\S]+)$/);
    if (ifMatch) {
      const condition = parseArg(ifMatch[1], event, target);
      return condition ? callNamedFunction(ifMatch[2], event, target) : true;
    }
    const clickMatch = text.match(/^document\.getElementById\((['"])([^'"]+)\1\)\.click\(\)$/);
    if (clickMatch) {
      const element = document.getElementById(clickMatch[2]);
      if (element) element.click();
      return true;
    }
    const callMatch = text.match(/^(?:window\.)?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\(([\s\S]*)\)$/);
    if (!callMatch) return true;
    const fn = callMatch[1].split('.').reduce((obj, key) => (obj ? obj[key] : undefined), window);
    if (typeof fn !== 'function') return true;
    const args = splitArgs(callMatch[2]).map(arg => parseArg(arg, event, target));
    const result = fn.apply(window, args);
    return shouldPreventDefault ? false : result;
  }

  function dispatch(eventName, event) {
    const attr = EVENT_MAP[eventName];
    const target = event.target.closest(`[${attr}]`);
    if (!target) return;
    const expression = target.getAttribute(attr) || '';
    let result = true;
    for (const statement of splitStatements(expression)) {
      const statementResult = callNamedFunction(statement, event, target);
      if (statementResult === false) result = false;
    }
    if (result === false) {
      event.preventDefault();
    }
  }

  document.addEventListener('click', event => dispatch('click', event));
  document.addEventListener('input', event => dispatch('input', event));
  document.addEventListener('change', event => dispatch('change', event));
  document.addEventListener('submit', event => dispatch('submit', event));
})();
