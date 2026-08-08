import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { X, AlertTriangle, Info } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from './Button';
import { scaleVariants, fadeVariants } from '../motion';

// ─── Base Dialog ───────────────────────────────────────────────────────────────

type DialogSize = 'sm' | 'md' | 'lg' | 'xl' | 'full';

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  description?: string;
  children: React.ReactNode;
  size?: DialogSize;
  closeOnBackdrop?: boolean;
  showClose?: boolean;
  footer?: React.ReactNode;
  className?: string;
}

const sizeMap: Record<DialogSize, string> = {
  sm:   'max-w-sm',
  md:   'max-w-md',
  lg:   'max-w-2xl',
  xl:   'max-w-4xl',
  full: 'max-w-[calc(100vw-2rem)] max-h-[calc(100vh-2rem)]',
};

export function Dialog({
  open, onClose, title, description, children, size = 'md',
  closeOnBackdrop = true, showClose = true, footer, className,
}: DialogProps) {
  const backdropRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Lock body scroll
  useEffect(() => {
    if (open) document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, [open]);

  const content = (
    <AnimatePresence>
      {open && (
        <motion.div
          ref={backdropRef}
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          variants={fadeVariants}
          initial="hidden"
          animate="visible"
          exit="exit"
          onClick={(e) => {
            if (closeOnBackdrop && e.target === backdropRef.current) onClose();
          }}
        >
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/65 backdrop-blur-[6px]" />

          {/* Panel */}
          <motion.div
            className={cn(
              'relative w-full rounded-2xl border border-monitor-border',
              'bg-monitor-surface shadow-modal flex flex-col',
              'max-h-[calc(100vh-3rem)] overflow-hidden',
              sizeMap[size],
              className
            )}
            variants={scaleVariants}
          >
            {/* Header */}
            {(title ?? showClose) && (
              <div className="flex items-start justify-between gap-3 px-6 pt-5 pb-4 border-b border-monitor-border flex-shrink-0">
                <div>
                  {title && (
                    <h2 className="font-display font-semibold text-[#E8F1FF] text-lg leading-tight">{title}</h2>
                  )}
                  {description && (
                    <p className="font-display text-vital-xs text-[#7A90AA] mt-0.5">{description}</p>
                  )}
                </div>
                {showClose && (
                  <motion.button
                    onClick={onClose}
                    className="flex-shrink-0 p-1.5 rounded-lg text-[#7A90AA] hover:text-[#E8F1FF] hover:bg-white/8 transition-colors mt-0.5"
                    whileHover={{ scale: 1.1 }}
                    whileTap={{ scale: 0.9 }}
                  >
                    <X size={18} />
                  </motion.button>
                )}
              </div>
            )}

            {/* Body */}
            <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>

            {/* Footer */}
            {footer && (
              <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-monitor-border flex-shrink-0">
                {footer}
              </div>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );

  return createPortal(content, document.body);
}

// ─── Confirmation Dialog ───────────────────────────────────────────────────────

interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: 'danger' | 'warning' | 'info';
  loading?: boolean;
}

const confirmIcons = {
  danger:  <AlertTriangle className="text-[#FF3B30]" size={24} />,
  warning: <AlertTriangle className="text-[#FF9500]" size={24} />,
  info:    <Info className="text-[#32ADE6]" size={24} />,
};

const confirmBg = {
  danger:  'bg-[rgba(255,59,48,0.1)] border-[rgba(255,59,48,0.3)]',
  warning: 'bg-[rgba(255,149,0,0.1)] border-[rgba(255,149,0,0.3)]',
  info:    'bg-[rgba(50,173,230,0.1)] border-[rgba(50,173,230,0.3)]',
};

export function ConfirmDialog({
  open, onClose, onConfirm, title, description,
  confirmLabel = 'Confirm', cancelLabel = 'Cancel',
  variant = 'danger', loading,
}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="sm"
      closeOnBackdrop={false}
      showClose={false}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={loading}>{cancelLabel}</Button>
          <Button variant={variant === 'danger' ? 'danger' : variant === 'warning' ? 'danger' : 'primary'}
                  onClick={onConfirm} loading={loading}>{confirmLabel}</Button>
        </>
      }
    >
      <div className="flex gap-4">
        <div className={cn('w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 border', confirmBg[variant])}>
          {confirmIcons[variant]}
        </div>
        <div>
          <h3 className="font-display font-semibold text-[#E8F1FF] text-base mb-1">{title}</h3>
          <p className="font-display text-vital-sm text-[#7A90AA] leading-relaxed">{description}</p>
        </div>
      </div>
    </Dialog>
  );
}
