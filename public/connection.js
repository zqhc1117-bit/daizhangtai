// Retry only startup reads. Saving, importing and deleting must never be replayed.
const transientStatuses = new Set([408, 502, 503, 504]);

function apiError(message, fields) {
  return Object.assign(new Error(message), { isApiError: true }, fields);
}

function uploadResponse(path, options, signal, report) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let idleTimer, stalled;
    const abort = () => xhr.abort();
    const finish = (callback, value) => {
      clearTimeout(idleTimer);
      signal?.removeEventListener('abort', abort);
      callback(value);
    };
    const watch = (ms, message) => {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => { stalled = message; abort(); }, ms);
    };
    const waitForResponse = () => watch(180000, '压缩包排版未收到响应，请重试');
    xhr.open(options.method || 'POST', path);
    xhr.withCredentials = options.credentials === 'include';
    for (const [key, value] of Object.entries(options.headers || {})) xhr.setRequestHeader(key, value);
    xhr.upload.onprogress = ({loaded, total}) => {
      watch(60000, '压缩包上传已停滞，请检查连接后重试');
      report({loaded, total, done: false});
    };
    xhr.upload.onload = ({loaded, total}) => {
      waitForResponse();
      report({loaded, total, done: true});
    };
    xhr.onprogress = waitForResponse;
    xhr.onload = () => finish(resolve, {
      status: xhr.status, ok: xhr.status >= 200 && xhr.status < 300,
      headers: {get: name => xhr.getResponseHeader(name)}, text: async () => xhr.responseText,
    });
    xhr.onerror = () => finish(reject, new Error('上传连接中断'));
    xhr.onabort = () => finish(reject, stalled
      ? apiError(stalled, {path: String(path).split(/[?#]/)[0], status: 0, kind: 'timeout', retryable: false})
      : new DOMException('上传已取消', 'AbortError'));
    if (signal?.aborted) return finish(reject, new DOMException('上传已取消', 'AbortError'));
    signal?.addEventListener('abort', abort, {once: true});
    watch(60000, '压缩包上传未收到响应，请检查连接后重试');
    xhr.send(options.body);
  });
}

export async function requestApi(path, options = {}) {
  const { timeoutMs = 0, expectJson = false, signal, onUploadProgress, ...requestOptions } = options;
  const readOnly = ['GET', 'HEAD'].includes((requestOptions.method || 'GET').toUpperCase());
  const requestPath = String(path).split(/[?#]/)[0];
  const controller = timeoutMs > 0 ? new AbortController() : null;
  let timedOut = false;
  const cancel = () => controller?.abort(signal.reason);
  if (controller && signal) {
    if (signal.aborted) cancel();
    else signal.addEventListener('abort', cancel, { once: true });
  }
  const timer = controller ? setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs) : null;
  let response;
  try {
    const requestSignal = controller?.signal || signal;
    response = onUploadProgress
      ? await uploadResponse(path, requestOptions, requestSignal, onUploadProgress)
      : await fetch(path, { ...requestOptions, signal: requestSignal });
    const contentType = (response.headers.get('content-type') || '').toLowerCase();
    const isJson = contentType.includes('application/json');
    const text = await response.text();
    let result = text;
    let invalidJson = false;
    if (isJson) {
      try { result = JSON.parse(text); } catch { invalidJson = true; }
    }
    const fields = { path: requestPath, status: response.status, externalResponse: !isJson };
    if (!response.ok) {
      const message = !invalidJson && typeof result?.error === 'string' ? result.error : `请求失败 (${response.status})`;
      throw apiError(message, {
        ...fields, kind: 'http', details: !invalidJson && typeof result === 'object' ? result?.details : undefined,
        retryable: readOnly && (transientStatuses.has(response.status) || (response.status === 404 && !isJson)),
      });
    }
    if (invalidJson || (expectJson && !isJson)) {
      throw apiError('收到的响应不是有效的工作台数据，请重试。', {
        ...fields, kind: 'invalid-response', retryable: readOnly,
      });
    }
    return result;
  } catch (error) {
    if (error.isApiError || signal?.aborted) throw error;
    throw apiError(timedOut ? '服务响应超时，请稍后重试。' : '网络连接中断，请检查网络后重试。', {
      path: requestPath, status: response?.status || 0, kind: timedOut ? 'timeout' : 'network', retryable: readOnly,
    });
  } finally {
    if (timer !== null) clearTimeout(timer);
    if (controller && signal) signal.removeEventListener('abort', cancel);
  }
}

export async function retryStartup(load, { onRetry = () => {}, delays = [1000, 2000, 4000] } = {}) {
  for (let attempt = 0; ; attempt++) {
    try { return await load(); } catch (error) {
      if (!error.retryable || attempt >= delays.length) throw error;
      onRetry({ attempt: attempt + 1, total: delays.length, error });
      await new Promise(resolve => setTimeout(resolve, delays[attempt]));
    }
  }
}

export function connectionMessage(error, hostname) {
  const local = /^(localhost$|127\.|\[?::1\]?$|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/i.test(hostname);
  if (error.status === 404 && error.externalResponse && !local) {
    return {
      title: '访问地址暂时不可用',
      message: '外网入口返回了 404，未能读取工作台数据。',
      hint: '请确认正在使用主机提供的最新地址。外网地址变更后，旧收藏夹需要更新。',
    };
  }
  if (error.status === 404) {
    return { title: '服务接口不存在', message: error.message, hint: '请刷新页面获取最新程序；若仍然出现，请联系维护人员核对程序版本。' };
  }
  if (error.status === 403) {
    return { title: '访问被拒绝', message: error.message, hint: '请确认访问地址和账号权限，或联系主机管理员。' };
  }
  return {
    title: error.kind === 'timeout' ? '服务响应超时' : '暂时无法加载工作台',
    message: error.message,
    hint: local ? '请确认代账主机上的服务仍在运行，网络恢复后可重新连接。' : '请检查网络及主机状态，并确认外网访问地址仍然有效。',
  };
}
