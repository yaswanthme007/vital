import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { slideRightVariants, slideBottomVariants, fadeVariants } from '../motion';

// ─── Types ─────────────────────────────────────────────────────────────────────

type DrawerPlacement = 'right' | 'left' | 'bottom';
type DrawerSize      = 'sm' | 'md' | 'lg' | 'xl';

interface DrawerProps {
  open:     boolean;
  onClose:  () => void;
  title?:   string;
  description?: string;
  placement?: DrawerPlacement;
  size?:    DrawerSize;
  children: React.ReactNode;
  footer?:  React.ReactNode;
  closeOnBackdrop?: boolean;
  className?: string;
}

// ─── Sizes ─────────────────────────────────────────────────────────────────────

const sizeSide: Record<DrawerSize, string> = {
  sm: 'max-w-xs',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-2xl',
};

const sizeBottom: Record<DrawerSize, string> = {
  sm: 'max-h-64',
  md: 'max-h-96',
  lg: 'max-h-[60vh]',
  xl: 'max-h-[80vh]',
};

const placementVariants = {
  right:  slideRightVariants,
  left:   { hidden: { opacity: 0, x: '-100%' }, visible: { opacity: 1, x: 0, transition: { duration: 0.32, ease: [0.16, 1, 0.3, 1] } }, exit: { opacity: 0, x: '-100%', transition: { duration: 0.22 } } },
  bottom: slideBottomVariants,
};

const placementClass: Record<DrawerPlacement, string> = {
  right:  'right-0 top-0 bottom-0 h-full',
  left:   'left-0 top-0 bottom-0 h-full',
  bottom: 'bottom-0 left-0 right-0 w-full',
};

// ─── Component ─────────────────────────────────────────────────────────────────

export function Drawer({
  open, onClose, title, description, placement = 'right', size = 'md',
  children, footer, closeOnBackdrop = true, className,
}: DrawerProps) {
  const backdropRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (open) document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, [open]);

  const isSide   = placement !== 'bottom';
  const sizeClass = isSide ? sizeSide[size] : sizeBottom[size];

  const content = (
    <AnimatePresence>
      {open && (
        <motion.div
          ref={backdropRef}
          className="fixed inset-0 z-50 flex"
          style={placement === 'right' ? { justifyContent: 'flex-end' } :
                 placement === 'left'  ? { justifyContent: 'flex-start' } :
                 { alignItems: 'flex-end' }}
          variants={fadeVariants}
          initial="hidden"
          animate="visible"
          exit="exit"
          onClick={(e) => { if (closeOnBackdrop && e.target === backdropRef.current) onClose(); }}
        >
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/60 backdrop-blur-[4px]" />

          {/* Drawer panel */}
          <motion.div
            className={cn(
              'relative flex flex-col',
              'bg-monitor-surface border-monitor-border',
              isSide ? `w-full border-l shadow-elevation-4` : 'w-full border-t rounded-t-2xl shadow-elevation-5',
              sizeClass,
              className
            )}
            variants={placementVariants[placement]}
          >
            {/* Drag handle for bottom sheet */}
            {placement === 'bottom' && (
              <div className="flex justify-center pt-3 pb-1">
                <div className="w-10 h-1 rounded-full bg-monitor-border" />
              </div>
            )}

            {/* Header */}
            {(title ?? description) && (
              <div className="flex items-start justify-between gap-3 px-6 pt-5 pb-4 border-b border-monitor-border flex-shrink-0">
                <div>
                  {title && <h2 className="font-display font-semibold text-[#E8F1FF] text-lg">{title}</h2>}
                  {description && <p className="font-display text-vital-xs text-[#7A90AA] mt-0.5">{description}</p>}
                </div>
                <motion.button
                  onClick={onClose}
                  className="p-1.5 rounded-lg text-[#7A90AA] hover:text-[#E8F1FF] hover:bg-white/8 transition-colors flex-shrink-0"
                  whileHover={{ scale: 1.1 }}
                  whileTap={{ scale: 0.9 }}
                >
                  <X size={18} />
                </motion.button>
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
