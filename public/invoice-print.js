import {escapeHtml as h} from './account-picker.js';
import {companyIcon} from './company-workspace.js';

const extensions = '.zip,.rar,.7z,.pdf,.jpg,.jpeg,.jfif,.png,.webp,.bmp,.gif,.tif,.tiff';
const accepted = new Set(extensions.split(','));
const archiveExtensions = new Set(['.zip', '.rar', '.7z']);
const maxFileBytes = 32 * 1024 * 1024;
const maxGroupBytes = 16 * 1024 * 1024;
const maxGroupFiles = 8;
const paperWidth = 210, slotHeight = 297 / 2;
const printableWidth = 182, printableHeight = 132;

function extension(file) {
  return file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
}

function* uploadGroups(files) {
  let group = [], bytes = 0;
  for (const file of files) {
    // Archives can already contain many tickets. Keep them and large originals separate.
    const separate = archiveExtensions.has(extension(file)) || file.size > maxGroupBytes;
    if (group.length && (separate || group.length === maxGroupFiles || bytes + file.size > maxGroupBytes)) {
      yield group;
      group = [];
      bytes = 0;
    }
    if (separate) {
      yield [file];
    } else {
      group.push(file);
      bytes += file.size;
    }
  }
  if (group.length) yield group;
}

export function renderInvoicePrint() {
  return `<section class="ip-workspace" aria-label="发票打印">
    <header class="ip-heading"><div><h1>发票打印</h1><p>A4 纵向，每页上下各一张发票</p></div>
      <button type="button" class="btn btn-primary ip-print" data-ip-action="print" disabled>${companyIcon('printer')}打印全部</button></header>
    <div class="ip-columns">
      <section class="ip-sources" aria-label="发票文件">
        <button type="button" class="ip-drop" data-ip-action="add">
          ${companyIcon('document')}<strong>选择文件或拖放到这里</strong>
          <span>PDF、图片、ZIP / RAR / 7Z 压缩包</span><small>支持多选，单个文件最大 32 MB</small>
        </button>
        <input type="file" data-ip-input hidden multiple accept="${extensions}">
        <p class="ip-progress" data-ip-progress role="status" hidden></p>
        <div class="ip-list-heading"><h2>待打印发票 <span data-ip-total>0 张</span></h2>
          <button type="button" class="ip-text-button" data-ip-action="clear" disabled>清空</button></div>
        <ol class="ip-list" data-ip-list></ol>
        <p class="ip-empty-list" data-ip-empty>每张图片、每页 PDF 各占一个票位。</p>
        <details class="ip-skipped" data-ip-skipped hidden><summary></summary><ul></ul></details>
      </section>
      <section class="ip-preview" aria-label="打印预览">
        <header class="ip-preview-head"><h2>打印预览</h2><span>A4 · 210 × 297 mm</span></header>
        <div class="ip-desk"><div class="ip-pack" data-ip-pack></div></div>
        <footer class="ip-controls"><button type="button" class="btn" data-ip-action="prev" disabled>上一页</button>
          <span data-ip-pager aria-live="polite">暂无发票</span><button type="button" class="btn" data-ip-action="next" disabled>下一页</button></footer>
      </section>
    </div>
  </section>`;
}

// Both screen and print use the same image and the same physical slot. When
// rotating by a quarter turn, fit the rotated bounds before positioning it.
function placeImage(item) {
  const sideways = item.turns % 2 === 1;
  const scale = Math.min(printableWidth / (sideways ? item.height : item.width),
                         printableHeight / (sideways ? item.width : item.height));
  item.img.style.width = `${100 * item.width * scale / paperWidth}%`;
  item.img.style.height = `${100 * item.height * scale / slotHeight}%`;
  item.img.style.transform = `translate(-50%, -50%) rotate(${90 * item.turns}deg)`;
}

export function bindInvoicePrint(root, clientId, ctx) {
  const find = selector => root.querySelector(selector);
  const action = key => find(`[data-ip-action="${key}"]`);
  const input = find('[data-ip-input]'), list = find('[data-ip-list]'), pack = find('[data-ip-pack]');
  const progress = find('[data-ip-progress]'), skipped = find('[data-ip-skipped]');
  const endpoint = `/api/books/${clientId}/invoice-print`;
  const items = [], issues = [];
  let current = 0, busy = false;

  function updateControls() {
    const count = Math.ceil(items.length / 2);
    current = Math.max(0, Math.min(current, count - 1));
    Array.from(pack.children).forEach((page, index) => page.classList.toggle('ip-current', index === current));
    for (const item of items.slice(current * 2, current * 2 + 2)) {
      if (item.previewURL && !item.img.hasAttribute('src')) item.img.src = item.previewURL;
    }
    find('[data-ip-total]').textContent = items.length + ' 张';
    find('[data-ip-pager]').textContent = count ? `第 ${current + 1} / ${count} 页` : '暂无发票';
    action('prev').disabled = current === 0;
    action('next').disabled = current + 1 >= count;
    action('add').disabled = input.disabled = busy;
    action('print').disabled = busy || !count;
    action('clear').disabled = busy || !(items.length || issues.length);
    for (const button of list.querySelectorAll('.ip-text-button')) button.disabled = busy;
    find('[data-ip-empty]').hidden = !!items.length;
  }

  function draw() {
    list.innerHTML = items.map(({label, source}, index) => `<li data-ip-index="${index}">
      <button type="button" class="ip-source" data-ip-action="locate" title="${h(source)}">
        <strong>${h(label)}</strong><small>第 ${Math.floor(index / 2) + 1} 页 · ${index % 2 ? '下' : '上'}半页</small></button>
      <button type="button" class="ip-text-button" data-ip-action="rotate" aria-label="顺时针旋转 ${h(label)}">旋转</button>
      <button type="button" class="ip-text-button" data-ip-action="remove" aria-label="移除 ${h(label)}">移除</button>
    </li>`).join('');
    const sheets = document.createDocumentFragment();
    for (let i = 0; i < items.length; i += 2) {
      const sheet = document.createElement('div');
      sheet.className = 'ip-sheet';
      sheet.setAttribute('aria-label', `第 ${i / 2 + 1} 张 A4 纸`);
      for (const item of items.slice(i, i + 2)) {
        const slot = document.createElement('div');
        slot.className = 'ip-slot';
        placeImage(item);
        slot.append(item.img);
        sheet.append(slot);
      }
      sheets.append(sheet);
    }
    pack.replaceChildren(sheets);
    if (!items.length) {
      pack.innerHTML = '<div class="ip-sheet ip-sample" aria-label="上下两张的版式示意"><div class="ip-slot"><span>第一张发票</span></div><div class="ip-slot"><span>第二张发票</span></div></div>';
    }
    skipped.hidden = !issues.length;
    skipped.querySelector('summary').textContent = `未加入打印（${issues.length} 项）`;
    skipped.querySelector('ul').innerHTML = issues.map(({name, reason}) => `<li><strong>${h(name)}</strong> · ${h(reason)}</li>`).join('');
    updateControls();
  }

  async function uploadGroup(group) {
    if (group.length > 1) {
      const body = new FormData();
      for (const file of group) body.append('file', file);
      try {
        const result = await ctx.api(endpoint, {method: 'POST', body, expectJson: true});
        if (Array.isArray(result.files) && result.files.length === group.length) return result.files;
      } catch (error) {
        if ([401, 403, 429].includes(error.status)) return group.map(() => ({error: error.message}));
        // Recover individual results when an older server or a bundle limit rejects the group.
      }
    }
    const results = [];
    for (const file of group) {
      if (!root.isConnected) break;
      try {
        const archive = archiveExtensions.has(extension(file));
        const query = new URLSearchParams({name: file.name});
        if (archive) query.set('preview', 'archive');
        const result = await ctx.api(`${endpoint}?${query}`, {
          method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file, expectJson: true,
          onUploadProgress: archive ? ({loaded, total, done}) => {
            if (!root.isConnected) return;
            const percent = Math.min(99, Math.floor(100 * loaded / (total || file.size)));
            progress.textContent = done ? '上传完成，正在解包和排版…' : `正在上传压缩包 ${percent}%`;
          } : undefined,
        });
        results.push(result);
      } catch (error) {
        results.push({error: error.message});
      }
    }
    return results;
  }

  function appendTickets(file, result) {
    if (result.error) {
      issues.push({name: file.name, reason: result.error});
      return;
    }
    if (items.length + result.pages.length > 240) {
      issues.push({name: file.name, reason: '待打印清单最多容纳 240 张，请先打印当前清单'});
      return;
    }
    for (const [index, page] of result.pages.entries()) {
      const suffix = page.count > 1 ? ` · 第 ${page.number}/${page.count} 页` : '';
      const label = page.name.split('/').at(-1).trim() + suffix;
      const img = new Image();
      img.alt = label;
      const item = {label, source: page.name + suffix, img, width: page.width, height: page.height, turns: 0};
      if (result.batch) {
        const asset = `${endpoint}/page?batch=${encodeURIComponent(result.batch)}&index=${index}`;
        item.previewURL = asset + '&size=preview';
        item.printURL = asset + '&size=print';
      } else {
        img.src = 'data:image/jpeg;base64,' + page.jpeg;
      }
      items.push(item);
    }
    issues.push(...result.skipped);
  }

  async function addFiles(files) {
    if (busy || !files.length) return;
    busy = true;
    updateControls();
    progress.hidden = false;
    try {
      const valid = files.filter(file => {
        const reason = !accepted.has(extension(file)) ? '不支持的文件格式'
          : (!file.size || file.size > maxFileBytes) ? '文件为空或超过 32 MB' : '';
        if (reason) issues.push({name: file.name, reason});
        return !reason;
      });
      let processed = files.length - valid.length;
      for (const group of uploadGroups(valid)) {
        if (!root.isConnected) return;
        progress.textContent = `正在排版 · 已处理 ${processed} / ${files.length} 个文件`;
        const results = await uploadGroup(group);
        if (!root.isConnected) return;
        results.forEach((result, index) => appendTickets(group[index], result));
        processed += group.length;
        if (processed < files.length) draw();
      }
    } finally {
      busy = false;
      if (root.isConnected) {
        progress.hidden = true;
        skipped.open = !!issues.length;
        draw();
      }
    }
  }

  async function printAll() {
    if (busy || !items.length) return;
    busy = true;
    updateControls();
    progress.hidden = false;
    try {
      for (let start = 0; start < items.length; start += 6) {
        if (!root.isConnected) return;
        progress.textContent = `正在加载高清票面 ${start} / ${items.length}`;
        const group = items.slice(start, start + 6);
        for (const item of group) {
          if (item.printURL && item.img.getAttribute('src') !== item.printURL) item.img.src = item.printURL;
        }
        let deadline;
        try {
          await Promise.race([
            Promise.all(group.map(item => item.img.decode())),
            new Promise((_, reject) => {
              deadline = setTimeout(() => reject(new Error('票面加载超时')), 180000);
            }),
          ]);
        } finally {
          clearTimeout(deadline);
        }
      }
      progress.hidden = true;
      if (root.isConnected) window.print();
    } catch {
      for (const item of items) {
        if (item.printURL && item.img.getAttribute('src') === item.printURL && !item.img.naturalWidth) {
          item.img.removeAttribute('src');
        }
      }
      if (root.isConnected) ctx.toast('高清票面加载失败，请重试。批次已过期时请重新上传', 'error');
    } finally {
      busy = false;
      if (root.isConnected) {
        progress.hidden = true;
        updateControls();
      }
    }
  }

  input.addEventListener('change', () => {
    const files = Array.from(input.files);
    input.value = '';
    addFiles(files);
  });
  const drop = action('add');
  for (const type of ['dragover', 'drop']) root.addEventListener(type, event => {
    event.preventDefault();
    drop.classList.toggle('ip-dragging', type === 'dragover' && !busy);
    if (type === 'drop') addFiles(Array.from(event.dataTransfer.files));
  });
  root.addEventListener('dragleave', event => {
    if (!root.contains(event.relatedTarget)) drop.classList.remove('ip-dragging');
  });
  root.addEventListener('click', event => {
    const button = event.target.closest('[data-ip-action]');
    if (!button || button.disabled) return;
    const index = Number(button.closest('li')?.dataset.ipIndex);
    switch (button.dataset.ipAction) {
      case 'add': input.click(); break;
      case 'print': printAll(); break;
      case 'prev': current--; updateControls(); break;
      case 'next': current++; updateControls(); break;
      case 'locate': current = Math.floor(index / 2); updateControls(); break;
      case 'rotate':
        items[index].turns = (items[index].turns + 1) % 4;
        placeImage(items[index]);
        current = Math.floor(index / 2);
        updateControls();
        break;
      case 'remove': items.splice(index, 1); draw(); break;
      case 'clear': items.length = issues.length = 0; draw(); break;
    }
  });
  draw();
}

if (typeof document !== 'undefined') document.addEventListener('keydown', event => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== 'p') return;
  const print = document.querySelector('.ip-workspace [data-ip-action="print"]');
  if (!print) return;
  event.preventDefault();
  print.click();
});
