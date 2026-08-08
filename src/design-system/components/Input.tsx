import { forwardRef, useState, useId } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Eye, EyeOff, Search, X } from 'lucide-react';
import { cn } from '@/lib/utils';

// ─── Types ─────────────────────────────────────────────────────────────────────

type InputState  = 'default' | 'error' | 'success' | 'warning';
type InputVariant= 'dark' | 'light';

interface BaseInputProps {
  label?:      string;
  helperText?: string;
  errorText?:  string;
  state?:      InputState;
  variant?:    InputVariant;
  prefix?:     React.ReactNode;
  suffix?:     React.ReactNode;
  className?:  string;
}

export interface InputProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'prefix' | 'suffix'>,
    BaseInputProps {}

// ─── Styles ────────────────────────────────────────────────────────────────────

const stateStyles: Record<InputState, { border: string; focus: string; helper: string }> = {
  default: {
    border: 'border-monitor-border focus-within:border-[#32ADE6]',
    focus:  'focus-within:shadow-input-focus',
    helper: 'text-[#3D5570]',
  },
  error: {
    border: 'border-[rgba(255,59,48,0.5)]',
    focus:  'shadow-input-error',
    helper: 'text-[#FF6B62]',
  },
  success: {
    border: 'border-[rgba(48,209,88,0.4)]',
    focus:  'shadow-input-success',
    helper: 'text-[#34C759]',
  },
  warning: {
    border: 'border-[rgba(255,149,0,0.4)]',
    focus:  '',
    helper: 'text-[#FFB340]',
  },
};

const variantStyles: Record<InputVariant, { wrap: string; input: string; label: string; helper: string }> = {
  dark: {
    wrap:   'bg-monitor-card',
    input:  'bg-transparent text-[#E8F1FF] placeholder-[#3D5570] caret-[#32ADE6]',
    label:  'text-[#7A90AA]',
    helper: 'text-[#3D5570]',
  },
  light: {
    wrap:   'bg-white',
    input:  'bg-transparent text-slate-900 placeholder-slate-400 caret-blue-500',
    label:  'text-slate-600',
    helper: 'text-slate-400',
  },
};

// ─── Input ─────────────────────────────────────────────────────────────────────

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({
    label, helperText, errorText, state = 'default', variant = 'dark',
    prefix, suffix, className, id: idProp, disabled, ...props
  }, ref) => {
    const generatedId = useId();
    const id = idProp ?? generatedId;
    const displayState: InputState = errorText ? 'error' : state;
    const s  = stateStyles[displayState];
    const v  = variantStyles[variant];
    const displayHelper = displayState === 'error' ? errorText : helperText;

    return (
      <div className={cn('flex flex-col gap-1.5', className)}>
        {label && (
          <label
            htmlFor={id}
            className={cn('font-display text-vital-xs uppercase tracking-wider', v.label)}
          >
            {label}
          </label>
        )}

        <div
          className={cn(
            'flex items-center gap-2 rounded-ds-lg border px-3 py-2',
            'transition-all duration-[150ms] ease-out',
            v.wrap, s.border, s.focus,
            disabled && 'opacity-40 pointer-events-none'
          )}
        >
          {prefix && (
            <span className="flex-shrink-0 text-[#3D5570]">{prefix}</span>
          )}
          <input
            ref={ref}
            id={id}
            disabled={disabled}
            className={cn(
              'flex-1 min-w-0 text-vital-base font-display',
              'focus:outline-none bg-transparent',
              v.input
            )}
            {...props}
          />
          {suffix && (
            <span className="flex-shrink-0 text-[#3D5570]">{suffix}</span>
          )}
        </div>

        <AnimatePresence>
          {displayHelper && (
            <motion.p
              key={displayHelper}
              className={cn('font-display text-vital-xs', s.helper)}
              initial={{ opacity: 0, y: -4, height: 0 }}
              animate={{ opacity: 1, y: 0,  height: 'auto' }}
              exit={{ opacity: 0,    y: -4, height: 0 }}
              transition={{ duration: 0.18 }}
            >
              {displayHelper}
            </motion.p>
          )}
        </AnimatePresence>
      </div>
    );
  }
);
Input.displayName = 'Input';

// ─── Password Input ────────────────────────────────────────────────────────────

export const PasswordInput = forwardRef<HTMLInputElement, InputProps>((props, ref) => {
  const [show, setShow] = useState(false);
  return (
    <Input
      ref={ref}
      type={show ? 'text' : 'password'}
      suffix={
        <button
          type="button"
          onClick={() => setShow(!show)}
          className="text-[#3D5570] hover:text-[#7A90AA] transition-colors"
          tabIndex={-1}
        >
          {show ? <EyeOff size={15} /> : <Eye size={15} />}
        </button>
      }
      {...props}
    />
  );
});
PasswordInput.displayName = 'PasswordInput';

// ─── Search Input ──────────────────────────────────────────────────────────────

interface SearchInputProps extends InputProps {
  onClear?: () => void;
}

export const SearchInput = forwardRef<HTMLInputElement, SearchInputProps>(
  ({ onClear, value, ...props }, ref) => (
    <Input
      ref={ref}
      value={value}
      prefix={<Search size={15} />}
      suffix={value && onClear ? (
        <button
          type="button"
          onClick={onClear}
          className="text-[#3D5570] hover:text-[#7A90AA] transition-colors"
        >
          <X size={14} />
        </button>
      ) : undefined}
      {...props}
    />
  )
);
SearchInput.displayName = 'SearchInput';

// ─── Textarea ──────────────────────────────────────────────────────────────────

export interface TextareaProps extends Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'prefix' | 'suffix'>, BaseInputProps {}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ label, helperText, errorText, state = 'default', variant = 'dark', className, id: idProp, disabled, prefix: _prefix, suffix: _suffix, ...props }, ref) => {
    const generatedId = useId();
    const id  = idProp ?? generatedId;
    const displayState: InputState = errorText ? 'error' : state;
    const s   = stateStyles[displayState];
    const v   = variantStyles[variant];
    const displayHelper = displayState === 'error' ? errorText : helperText;

    return (
      <div className={cn('flex flex-col gap-1.5', className)}>
        {label && (
          <label htmlFor={id} className={cn('font-display text-vital-xs uppercase tracking-wider', v.label)}>
            {label}
          </label>
        )}
        <div className={cn('rounded-ds-lg border transition-all duration-150', v.wrap, s.border, s.focus, disabled && 'opacity-40')}>
          <textarea
            ref={ref}
            id={id}
            disabled={disabled}
            className={cn(
              'w-full min-h-[80px] px-3 py-2.5 text-vital-base font-display resize-none',
              'focus:outline-none bg-transparent rounded-ds-lg',
              v.input
            )}
            {...props}
          />
        </div>
        {displayHelper && (
          <p className={cn('font-display text-vital-xs', s.helper)}>{displayHelper}</p>
        )}
      </div>
    );
  }
);
Textarea.displayName = 'Textarea';

// ─── Select ────────────────────────────────────────────────────────────────────

export interface SelectProps extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'prefix' | 'suffix'>, BaseInputProps {
  options: { value: string; label: string }[];
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ label, helperText, errorText, state = 'default', variant = 'dark', options, className, id: idProp, prefix: _prefix, suffix: _suffix, ...props }, ref) => {
    const generatedId = useId();
    const id  = idProp ?? generatedId;
    const displayState: InputState = errorText ? 'error' : state;
    const s   = stateStyles[displayState];
    const v   = variantStyles[variant];

    return (
      <div className={cn('flex flex-col gap-1.5', className)}>
        {label && (
          <label htmlFor={id} className={cn('font-display text-vital-xs uppercase tracking-wider', v.label)}>
            {label}
          </label>
        )}
        <div className={cn('rounded-ds-lg border transition-all duration-150', v.wrap, s.border, s.focus)}>
          <select
            ref={ref}
            id={id}
            className={cn(
              'w-full px-3 py-2 text-vital-base font-display',
              'focus:outline-none bg-transparent appearance-none cursor-pointer',
              v.input
            )}
            {...props}
          >
            {options.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        {(errorText ?? helperText) && (
          <p className={cn('font-display text-vital-xs', s.helper)}>{errorText ?? helperText}</p>
        )}
      </div>
    );
  }
);
Select.displayName = 'Select';

// ─── Checkbox ─────────────────────────────────────────────────────────────────

interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: string;
  description?: string;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ label, description, className, ...props }, ref) => (
    <label className={cn('flex items-start gap-2.5 cursor-pointer group', className)}>
      <span className="relative flex-shrink-0 mt-0.5">
        <input
          ref={ref}
          type="checkbox"
          className="sr-only peer"
          {...props}
        />
        <span className={cn(
          'block w-4 h-4 rounded border border-monitor-border bg-monitor-card',
          'transition-all duration-150',
          'peer-checked:bg-[#32ADE6] peer-checked:border-[#32ADE6]',
          'peer-focus-visible:ring-2 peer-focus-visible:ring-[#32ADE6] peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-monitor-bg',
          'group-hover:border-monitor-border-bright'
        )} />
        <svg
          className="absolute inset-0 w-4 h-4 text-white opacity-0 peer-checked:opacity-100 pointer-events-none transition-opacity duration-100"
          viewBox="0 0 16 16" fill="none"
        >
          <path d="M3.5 8l3 3 6-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      {(label ?? description) && (
        <span className="flex flex-col gap-0.5">
          {label && <span className="font-display text-vital-base text-[#E8F1FF] leading-tight">{label}</span>}
          {description && <span className="font-display text-vital-xs text-[#3D5570]">{description}</span>}
        </span>
      )}
    </label>
  )
);
Checkbox.displayName = 'Checkbox';

// ─── Toggle / Switch ──────────────────────────────────────────────────────────

interface ToggleProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: string;
  accentColor?: string;
}

export const Toggle = forwardRef<HTMLInputElement, ToggleProps>(
  ({ label, accentColor = '#32ADE6', className, ...props }, ref) => (
    <label className={cn('flex items-center gap-3 cursor-pointer group', className)}>
      <span className="relative flex-shrink-0">
        <input ref={ref} type="checkbox" className="sr-only peer" {...props} />
        <span className={cn(
          'block w-10 h-5.5 rounded-full border border-monitor-border bg-monitor-card',
          'transition-all duration-200',
          'peer-checked:border-transparent',
          'peer-focus-visible:ring-2 peer-focus-visible:ring-[#32ADE6] peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-monitor-bg'
        )}
        style={{}}
        />
        <span
          className="absolute left-0.5 top-0.5 block w-4 h-4 rounded-full bg-[#3D5570] shadow transition-all duration-200 peer-checked:translate-x-[18px] peer-checked:bg-white"
          style={{}}
        />
        <style>{`input:checked ~ span { background-color: ${accentColor}; }`}</style>
      </span>
      {label && <span className="font-display text-vital-base text-[#E8F1FF]">{label}</span>}
    </label>
  )
);
Toggle.displayName = 'Toggle';
