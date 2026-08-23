/* @ds-bundle: {"format":4,"namespace":"WingmanDesignSystem_a738fa","components":[{"name":"Button","sourcePath":"components/buttons/Button.jsx"},{"name":"PopCard","sourcePath":"components/cards/PopCard.jsx"},{"name":"SoftCard","sourcePath":"components/cards/SoftCard.jsx"},{"name":"ChatBubble","sourcePath":"components/chat/ChatBubble.jsx"},{"name":"ChatStarterButton","sourcePath":"components/chat/ChatBubble.jsx"},{"name":"ProgressTrack","sourcePath":"components/feedback/ProgressTrack.jsx"},{"name":"ProgressLegend","sourcePath":"components/feedback/ProgressTrack.jsx"},{"name":"StatusPill","sourcePath":"components/feedback/StatusPill.jsx"},{"name":"Badge","sourcePath":"components/feedback/StatusPill.jsx"},{"name":"Select","sourcePath":"components/forms/Select.jsx"},{"name":"TextArea","sourcePath":"components/forms/TextArea.jsx"},{"name":"TextField","sourcePath":"components/forms/TextField.jsx"},{"name":"TopNav","sourcePath":"components/navigation/TopNav.jsx"},{"name":"Modal","sourcePath":"components/overlays/Modal.jsx"}],"sourceHashes":{"components/buttons/Button.jsx":"c3b2922b531f","components/cards/PopCard.jsx":"06913b3666ae","components/cards/SoftCard.jsx":"f0e89666a71e","components/chat/ChatBubble.jsx":"8f32d85b64d4","components/feedback/ProgressTrack.jsx":"534846efa609","components/feedback/StatusPill.jsx":"5bef0200722c","components/forms/Select.jsx":"07c13cebd1c1","components/forms/TextArea.jsx":"8aa622485062","components/forms/TextField.jsx":"d59f36e17924","components/navigation/TopNav.jsx":"b7e006780d40","components/overlays/Modal.jsx":"e20badfaf68b"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.WingmanDesignSystem_a738fa = window.WingmanDesignSystem_a738fa || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/buttons/Button.jsx
try { (() => {
function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  disabled = false,
  icon,
  children,
  onClick
}) {
  const isPop = variant !== 'ghost';
  const pad = size === 'sm' ? '8px 16px' : '12px 24px';
  const fontSize = size === 'sm' ? 12 : 13;
  const base = {
    fontFamily: 'var(--font-body)',
    fontWeight: 800,
    cursor: disabled ? 'not-allowed' : 'pointer',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 8,
    justifyContent: 'center',
    opacity: disabled ? 0.5 : loading ? 0.65 : 1,
    pointerEvents: loading ? 'none' : 'auto'
  };
  let style;
  if (variant === 'primary') style = {
    ...base,
    background: 'var(--accent-primary-soft)',
    color: '#fff',
    border: 'none',
    borderRadius: 999,
    padding: pad,
    fontSize: fontSize,
    boxShadow: 'var(--shadow-pop-btn)'
  };else if (variant === 'secondary') style = {
    ...base,
    background: '#fff',
    color: 'var(--text-body)',
    border: '2px solid var(--border-ink)',
    borderRadius: 12,
    padding: pad,
    fontSize: fontSize,
    boxShadow: 'var(--shadow-pop-ink)'
  };else style = {
    ...base,
    background: 'none',
    color: 'var(--text-muted)',
    border: 'none',
    borderRadius: 0,
    padding: '4px 0',
    fontSize: fontSize,
    fontWeight: 600,
    textDecoration: 'underline'
  };
  return React.createElement('button', {
    onClick,
    disabled,
    className: isPop ? 'pop-btn' : undefined,
    style
  }, loading && React.createElement('span', {
    style: {
      width: 14,
      height: 14,
      border: '2px solid currentColor',
      borderTopColor: 'transparent',
      borderRadius: '50%',
      display: 'inline-block',
      animation: 'spin .6s linear infinite'
    }
  }), icon && !loading && icon, children);
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/buttons/Button.jsx", error: String((e && e.message) || e) }); }

// components/cards/PopCard.jsx
try { (() => {
function PopCard({
  children,
  style
}) {
  return React.createElement('div', {
    className: 'pop-card',
    style: {
      padding: 20,
      background: '#fff',
      ...style
    }
  }, children);
}
Object.assign(__ds_scope, { PopCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/cards/PopCard.jsx", error: String((e && e.message) || e) }); }

// components/cards/SoftCard.jsx
try { (() => {
function SoftCard({
  children,
  onClick,
  style
}) {
  return React.createElement('div', {
    className: 'card-soft',
    onClick,
    style: {
      padding: 24,
      ...style
    }
  }, children);
}
Object.assign(__ds_scope, { SoftCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/cards/SoftCard.jsx", error: String((e && e.message) || e) }); }

// components/chat/ChatBubble.jsx
try { (() => {
function ChatBubble({
  from = 'bot',
  children
}) {
  const isUser = from === 'user';
  return React.createElement('div', {
    style: {
      background: isUser ? '#E0E7FF' : '#fff',
      border: '2px solid #0F172A',
      borderRadius: isUser ? '14px 14px 2px 14px' : '14px 14px 14px 2px',
      padding: '10px 14px',
      fontSize: 13,
      fontWeight: 600,
      maxWidth: '85%',
      alignSelf: isUser ? 'flex-end' : 'flex-start',
      marginLeft: isUser ? 'auto' : 0,
      fontFamily: 'var(--font-body)',
      color: 'var(--text-body)'
    }
  }, children);
}
function ChatStarterButton({
  children,
  onClick
}) {
  return React.createElement('button', {
    onClick,
    style: {
      background: '#fff',
      border: '2px solid #0F172A',
      borderRadius: 14,
      padding: '10px 14px',
      fontSize: 13,
      fontWeight: 600,
      width: '100%',
      textAlign: 'left',
      cursor: 'pointer',
      fontFamily: 'var(--font-body)'
    }
  }, children);
}
Object.assign(__ds_scope, { ChatBubble, ChatStarterButton });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/chat/ChatBubble.jsx", error: String((e && e.message) || e) }); }

// components/feedback/ProgressTrack.jsx
try { (() => {
function ProgressTrack({
  segments
}) {
  const total = segments.reduce((s, x) => s + x.value, 0) || 1;
  return React.createElement('div', {
    style: {
      display: 'flex',
      width: '100%',
      height: 22,
      border: '2px solid var(--border-strong)',
      borderRadius: 999,
      overflow: 'hidden',
      background: 'var(--surface-page)'
    }
  }, segments.map((s, i) => React.createElement('div', {
    key: i,
    style: {
      height: '100%',
      width: s.value / total * 100 + '%',
      background: s.color,
      transition: 'width .3s ease'
    }
  })));
}
function ProgressLegend({
  items
}) {
  return React.createElement('div', {
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 16
    }
  }, items.map((it, i) => React.createElement('div', {
    key: i,
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      color: 'var(--text-brand)',
      fontSize: 12,
      fontFamily: 'var(--font-body)',
      fontWeight: 600
    }
  }, React.createElement('span', {
    style: {
      width: 10,
      height: 10,
      borderRadius: 999,
      background: it.color,
      flexShrink: 0
    }
  }), it.label)));
}
Object.assign(__ds_scope, { ProgressTrack, ProgressLegend });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/ProgressTrack.jsx", error: String((e && e.message) || e) }); }

// components/feedback/StatusPill.jsx
try { (() => {
function StatusPill({
  kind = 'opp',
  status = 'not_started',
  children
}) {
  const map = {
    'opp-in_progress': {
      bg: '#D8F0E9',
      color: '#1A6E58'
    },
    'opp-not_started': {
      bg: '#CDEAF2',
      color: '#00697A'
    },
    'opp-completed': {
      bg: '#FCE9D0',
      color: '#8A4A0E'
    },
    'task-not_started': {
      bg: '#fbd1a2',
      color: '#8A4A0E'
    },
    'task-in_progress': {
      bg: '#CDEAF2',
      color: '#00697A'
    },
    'task-completed': {
      bg: '#D8F0E9',
      color: '#1A6E58'
    }
  };
  const c = map[kind + '-' + status] || map['opp-not_started'];
  return React.createElement('span', {
    style: {
      padding: '3px 10px',
      borderRadius: 999,
      border: '2px solid var(--border-strong)',
      fontWeight: 800,
      fontSize: 10,
      textTransform: 'uppercase',
      whiteSpace: 'nowrap',
      background: c.bg,
      color: c.color,
      fontFamily: 'var(--font-body)'
    }
  }, children);
}
function Badge({
  tone = 'amber',
  children
}) {
  const map = {
    amber: {
      bg: '#FDE047',
      color: '#1a2540'
    },
    orange: {
      bg: '#f79256',
      color: '#fff'
    },
    navy: {
      bg: '#1d4e89',
      color: '#fff'
    }
  };
  const c = map[tone];
  return React.createElement('span', {
    style: {
      background: c.bg,
      color: c.color,
      fontSize: 10,
      fontWeight: 800,
      textTransform: 'uppercase',
      padding: '2px 8px',
      borderRadius: 999,
      border: '2px solid var(--border-ink)'
    }
  }, children);
}
Object.assign(__ds_scope, { StatusPill, Badge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/StatusPill.jsx", error: String((e && e.message) || e) }); }

// components/forms/Select.jsx
try { (() => {
const fieldStyle = {
  borderRadius: 16,
  border: 'none',
  background: '#eef0fb',
  fontFamily: "'Poppins',sans-serif",
  padding: '12px',
  width: '100%',
  boxSizing: 'border-box',
  fontSize: 14,
  color: 'var(--text-body)'
};
function Select({
  label,
  options = []
}) {
  return React.createElement('div', null, label && React.createElement('label', {
    style: {
      display: 'block',
      fontWeight: 700,
      fontSize: 11,
      textTransform: 'uppercase',
      letterSpacing: '.05em',
      color: 'var(--text-muted)',
      marginBottom: 8
    }
  }, label), React.createElement('select', {
    style: fieldStyle
  }, options.map((o, i) => React.createElement('option', {
    key: i
  }, o))));
}
Object.assign(__ds_scope, { Select });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Select.jsx", error: String((e && e.message) || e) }); }

// components/forms/TextArea.jsx
try { (() => {
const fieldStyle = {
  borderRadius: 16,
  border: 'none',
  background: '#eef0fb',
  fontFamily: "'Poppins',sans-serif",
  padding: '12px',
  width: '100%',
  boxSizing: 'border-box',
  fontSize: 14,
  color: 'var(--text-body)'
};
function TextArea({
  label,
  placeholder,
  rows = 5
}) {
  return React.createElement('div', null, label && React.createElement('label', {
    style: {
      display: 'block',
      fontWeight: 700,
      fontSize: 11,
      textTransform: 'uppercase',
      letterSpacing: '.05em',
      color: 'var(--text-muted)',
      marginBottom: 8
    }
  }, label), React.createElement('textarea', {
    placeholder,
    rows,
    style: {
      ...fieldStyle,
      minHeight: 120,
      resize: 'vertical'
    }
  }));
}
Object.assign(__ds_scope, { TextArea });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/TextArea.jsx", error: String((e && e.message) || e) }); }

// components/forms/TextField.jsx
try { (() => {
const fieldStyle = {
  borderRadius: 16,
  border: 'none',
  background: '#eef0fb',
  fontFamily: "'Poppins',sans-serif",
  padding: '12px',
  width: '100%',
  boxSizing: 'border-box',
  fontSize: 14,
  color: 'var(--text-body)'
};
function TextField({
  label,
  placeholder,
  type = 'text'
}) {
  return React.createElement('div', null, label && React.createElement('label', {
    style: {
      display: 'block',
      fontWeight: 700,
      fontSize: 11,
      textTransform: 'uppercase',
      letterSpacing: '.05em',
      color: 'var(--text-muted)',
      marginBottom: 8
    }
  }, label), React.createElement('input', {
    type,
    placeholder,
    style: fieldStyle
  }));
}
Object.assign(__ds_scope, { TextField });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/TextField.jsx", error: String((e && e.message) || e) }); }

// components/navigation/TopNav.jsx
try { (() => {
function TopNav({
  active = 'home',
  items,
  onNavigate
}) {
  return React.createElement('div', {
    style: {
      borderRadius: 999,
      padding: '8px 8px 8px 12px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 8,
      background: '#1d4e89',
      boxShadow: '0 10px 25px -5px rgba(29,78,137,.45)'
    }
  }, React.createElement('div', {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      color: '#fff',
      fontFamily: 'var(--font-display)',
      fontWeight: 800,
      fontSize: 16
    }
  }, 'Wingman'), React.createElement('nav', {
    style: {
      display: 'flex',
      gap: 6
    }
  }, items.map(it => React.createElement('button', {
    key: it.key,
    onClick: () => onNavigate && onNavigate(it.key),
    style: {
      border: 'none',
      cursor: 'pointer',
      fontWeight: 700,
      fontSize: 13,
      padding: '8px 16px',
      borderRadius: 999,
      fontFamily: 'var(--font-body)',
      background: active === it.key ? '#f79256' : 'transparent',
      color: active === it.key ? '#fff' : '#B7D3E8',
      transition: 'background-color .15s,color .15s'
    }
  }, it.label))), React.createElement('div', {
    style: {
      width: 36,
      height: 36,
      borderRadius: 999,
      background: '#00b2ca',
      color: '#fff',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center'
    }
  }, '\uD83D\uDC64'));
}
Object.assign(__ds_scope, { TopNav });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/TopNav.jsx", error: String((e && e.message) || e) }); }

// components/overlays/Modal.jsx
try { (() => {
function Modal({
  open,
  onClose,
  children
}) {
  if (!open) return null;
  return React.createElement('div', {
    style: {
      position: 'fixed',
      inset: 0,
      background: 'rgba(15,23,42,.55)',
      display: 'flex',
      alignItems: 'flex-start',
      justifyContent: 'center',
      padding: '40px 16px',
      overflowY: 'auto',
      zIndex: 200
    },
    onClick: onClose
  }, React.createElement('div', {
    className: 'card-soft',
    style: {
      maxWidth: 640,
      width: '100%',
      padding: 32
    },
    onClick: e => e.stopPropagation()
  }, children));
}
Object.assign(__ds_scope, { Modal });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/overlays/Modal.jsx", error: String((e && e.message) || e) }); }

__ds_ns.Button = __ds_scope.Button;

__ds_ns.PopCard = __ds_scope.PopCard;

__ds_ns.SoftCard = __ds_scope.SoftCard;

__ds_ns.ChatBubble = __ds_scope.ChatBubble;

__ds_ns.ChatStarterButton = __ds_scope.ChatStarterButton;

__ds_ns.ProgressTrack = __ds_scope.ProgressTrack;

__ds_ns.ProgressLegend = __ds_scope.ProgressLegend;

__ds_ns.StatusPill = __ds_scope.StatusPill;

__ds_ns.Badge = __ds_scope.Badge;

__ds_ns.Select = __ds_scope.Select;

__ds_ns.TextArea = __ds_scope.TextArea;

__ds_ns.TextField = __ds_scope.TextField;

__ds_ns.TopNav = __ds_scope.TopNav;

__ds_ns.Modal = __ds_scope.Modal;

})();
