import { forwardRef } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { Spinner } from './Spinner';

// ─── Types ─────────────────────────────────────────────────────────────────────

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success' | 'outline' | 'clinical';
export type ButtonSize    = 'xs' | 'sm' | 'md' | 'lg';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?:   ButtonVariant;
  size?:      ButtonSize;
  loading?:   boolean;
  icon?:      React.ReactNode;
  iconRight?: React.ReactNode;
  iconOnly?:  boolean;
}

// ─── Styles ────────────────────────────────────────────────────────────────────

const base = [
  'relative inline-flex items-center justify-center font-display font-medium',
  'transition-all duration-[120ms] ease-out',
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#32ADE6] focus-visible:ring-offset-2 focus-visible:ring-offset-monitor-bg',
  'disabled:opacity-40 disabled:pointer-events-none select-none',
  'rounded-ds-lg overflow-hidden',
].join(' ');

const variants: Record<ButtonVariant, string> = {
  primary:  'bg-[#32ADE6] text-white hover:bg-[#3BBCF0] active:bg-[#1E9AD4] shadow-info-glow hover:shadow-[0_6px_24px_rgba(50,173,230,0.5)]',
  secondary:'bg-monitor-card text-[#E8F1FF] border border-monitor-border hover:bg-monitor-card-hover hover:border-monitor-border-bright active:bg-[#0D1829]',
  ghost:    'text-[#7A90AA] hover:text-[#E8F1FF] hover:bg-white/[0.06] active:bg-white/[0.03]',
  danger:   'bg-[#FF3B30] text-white hover:bg-[#FF5344] active:bg-[#E0291E] shadow-critical hover:shadow-[0_6px_24px_rgba(255,59,48,0.5)]',
  success:  'bg-[#30D158] text-[#070D1A] hover:bg-[#38E563] active:bg-[#28B84B] font-semibold shadow-success-glow hover:shadow-[0_6px_24px_rgba(48,209,88,0.5)]',
  outline:  'border border-monitor-border-bright text-[#E8F1FF] hover:bg-white/[0.05] hover:border-[#32ADE6] active:bg-white/[0.03]',
  clinical: 'bg-[#00FF88] text-[#070D1A] hover:bg-[#22FF99] active:bg-[#00D970] font-semibold shadow-vital-hr hover:shadow-[0_6px_24px_rgba(0,255,136,0.5)]',
};

const sizes: Record<ButtonSize, string> = {
  xs: 'h-7  px-2.5 text-[11px] gap-1.5',
  sm: 'h-8  px-3   text-xs     gap-2',
  md: 'h-9  px-4   text-[13px] gap-2',
  lg: 'h-11 px-6   text-sm     gap-2.5',
};

const iconOnlySizes: Record<ButtonSize, string> = {
  xs: 'h-7  w-7',
  sm: 'h-8  w-8',
  md: 'h-9  w-9',
  lg: 'h-11 w-11',
};

// ─── Component ─────────────────────────────────────────────────────────────────

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'secondary', size = 'md', loading, icon, iconRight, iconOnly, children, className, disabled, ...props }, ref) => {
    const isDisabled = disabled || loading;

    return (
      <motion.div
        style={{ display: 'inline-flex' }}
        whileHover={{ scale: isDisabled ? 1 : 1.025 }}
        whileTap={{ scale: isDisabled ? 1 : 0.965 }}
        transition={{ duration: 0.12, ease: [0, 0, 0.2, 1] }}
      >
        <button
          ref={ref}
          className={cn(base, variants[variant], iconOnly ? iconOnlySizes[size] : sizes[size], className)}
          disabled={isDisabled}
          aria-busy={loading}
          {...props}
        >
          {/* Shimmer on hover for primary/clinical/success */}
          {(variant === 'primary' || variant === 'clinical' || variant === 'success') && (
            <span className="absolute inset-0 opacity-0 hover:opacity-100 transition-opacity duration-300"
                  style={{ background: 'linear-gradient(105deg, transparent 40%, rgba(255,255,255,0.12) 50%, transparent 60%)' }} />
          )}

          {loading ? (
            <Spinner size={size === 'xs' ? 'xs' : size === 'sm' ? 'sm' : 'md'} color="currentColor" />
          ) : (
            icon && <span className="flex-shrink-0">{icon}</span>
          )}

          {!iconOnly && children && (
            <span className={cn(loading ? 'opacity-0' : 'opacity-100')}>{children}</span>
          )}

          {!loading && iconRight && (
            <span className="flex-shrink-0 ml-auto pl-1">{iconRight}</span>
          )}
        </button>
      </motion.div>
    );
  }
);
Button.displayName = 'Button';

// ─── Icon Button shorthand ─────────────────────────────────────────────────────

export const IconButton = forwardRef<HTMLButtonElement, ButtonProps>(
  (props, ref) => <Button ref={ref} iconOnly {...props} />
);
IconButton.displayName = 'IconButton';
