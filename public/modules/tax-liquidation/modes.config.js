// Remove either entry to lock the workspace to the remaining mode after refresh.
export const ENABLED_MODES = ['quick', 'full'];
export const MODE_LABELS = {quick: '极速模式 · 小微/空壳', full: '完整模式 · 实体经营'};
export function availableModes(modes = ENABLED_MODES) {
  return [...new Set(modes.filter(mode => Object.hasOwn(MODE_LABELS, mode)))];
}
export function resolveMode(mode, modes = ENABLED_MODES) {
  const enabled = availableModes(modes);
  if (!enabled.length) throw new Error('请在 modes.config.js 中启用至少一种清算模式。');
  return enabled.includes(mode) ? mode : enabled[0];
}
