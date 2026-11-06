import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/utils'
const variants = cva('button', { variants: { variant: { default: 'primary', outline: 'outline', ghost: 'ghost', destructive: 'destructive' }, size: { default: '', sm: 'small', icon: 'icon-button' } }, defaultVariants: { variant: 'default', size: 'default' } })
export function Button({ className, variant, size, asChild = false, ...props }: React.ComponentProps<'button'> & VariantProps<typeof variants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'button'
  return <Comp className={cn(variants({ variant, size }), className)} {...props} />
}
