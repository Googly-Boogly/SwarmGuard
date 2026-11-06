import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
export function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)) }
export const date = (value?: string | null) => value ? new Date(value).toLocaleString() : '—'
export const short = (value: string) => value.replaceAll('_', ' ')
export const metric = (value: unknown) => value === null || value === undefined ? 'Undefined' : typeof value === 'number' ? Number.isInteger(value) ? value.toString() : value.toFixed(3) : String(value)
