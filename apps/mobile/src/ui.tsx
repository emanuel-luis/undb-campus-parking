import type { LotAvailability } from './types'
import { ProblemError, type LiveStatus } from '@undb-campus-parking/api-client'
import type { ReactNode } from 'react'
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View, type TextProps } from 'react-native'
import { formatInt } from './format'
import { lotStatus, statusColor, statusLabel } from './status'
import { colors, fonts, TOUCH } from './theme'

export const NOTICE = 'Projeto acadêmico não oficial, sem vínculo com a UNDB nem endosso dela.'

export function T({ variant = 'body', style, ...rest }: TextProps & { variant?: 'title' | 'subtitle' | 'body' | 'muted' }) {
  return <Text {...rest} style={[s[variant], style]} />
}

export function Footer() {
  return (
    <View style={s.footer}>
      <T variant="muted" style={s.center}>
        {NOTICE}
      </T>
    </View>
  )
}

/** Scrollable screen body with the footer notice; pass onRefresh for pull to refresh. */
export function Screen({ children, refreshing, onRefresh }: { children: ReactNode; refreshing?: boolean; onRefresh?: () => void }) {
  return (
    <ScrollView
      style={s.screen}
      contentContainerStyle={s.content}
      refreshControl={onRefresh ? <RefreshControl refreshing={refreshing ?? false} onRefresh={onRefresh} colors={[colors.primary]} /> : undefined}
    >
      {children}
      <Footer />
    </ScrollView>
  )
}

/** pt-BR message for any API failure: a ProblemError or a network TypeError. */
export function errorMessage(error: unknown): string {
  if (error instanceof ProblemError) {
    if (error.code === 'warehouse-not-ready') {
      return 'Os relatórios ainda estão sendo preparados. Tente de novo em alguns minutos.'
    }
    const id = error.requestId ? ` (id ${error.requestId})` : ''
    return `Erro ${error.status}: ${error.detail || error.title}${id}`
  }
  return 'Sem conexão com a API. Verifique a rede e o endereço configurado.'
}

export function Loading() {
  return (
    <View style={s.state} accessibilityRole="progressbar" accessibilityLabel="Carregando">
      <ActivityIndicator color={colors.primary} />
      <T variant="muted">Carregando…</T>
    </View>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <View style={s.state} accessibilityRole="alert">
      <T>{errorMessage(error)}</T>
      {onRetry ? <Button label="Tentar de novo" onPress={onRetry} /> : null}
    </View>
  )
}

export function Empty({ message }: { message: string }) {
  return (
    <View style={s.state}>
      <T variant="muted">{message}</T>
    </View>
  )
}

export function Button({ label, onPress, selected = false }: { label: string; onPress: () => void; selected?: boolean }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected }}
      onPress={onPress}
      style={[s.button, selected && s.buttonSelected]}
    >
      <Text style={[s.buttonText, selected && s.buttonTextSelected]}>{label}</Text>
    </Pressable>
  )
}

const LIVE_LABEL: Record<LiveStatus, string> = {
  open: 'Ao vivo',
  connecting: 'Reconectando',
  polling: 'Sem conexão: atualizando a cada 10 s',
  paused: 'Pausado',
  closed: 'Desconectado',
}

export function StatusChip({ status }: { status: LiveStatus }) {
  const color = status === 'open' ? colors.free : status === 'polling' ? colors.busy : colors.muted
  return (
    <View style={[s.chip, { borderColor: color }]} accessibilityLabel={`Conexão: ${LIVE_LABEL[status]}`}>
      <View style={[s.dot, { backgroundColor: color }]} />
      <Text style={[s.chipText, { color }]}>{LIVE_LABEL[status]}</Text>
    </View>
  )
}

export function lotLabel(lot: LotAvailability): string {
  if (lot.capacity === null) return `${lot.name}: ${formatInt(lot.occupied)} ocupadas, sem capacidade definida`
  return `${lot.name}: ${formatInt(lot.free)} vagas livres de ${formatInt(lot.capacity)}`
}

export function LotCard({ lot, onPress }: { lot: LotAvailability; onPress?: () => void }) {
  const status = lotStatus(lot.free, lot.occupancy_pct)
  const color = statusColor[status]
  const pct = Math.min(100, lot.occupancy_pct ?? 0)
  return (
    <Pressable
      style={s.card}
      onPress={onPress}
      disabled={!onPress}
      accessibilityRole={onPress ? 'button' : 'summary'}
      accessibilityLabel={`${lotLabel(lot)}. ${statusLabel[status]}`}
    >
      <T variant="subtitle">{lot.name}</T>
      <View style={s.row}>
        <Text style={[s.big, { color }]}>{formatInt(lot.free)}</Text>
        <T variant="muted"> vagas livres de {formatInt(lot.capacity)}</T>
      </View>
      <View style={s.track}>
        <View style={[s.fill, { width: `${pct}%`, backgroundColor: color }]} />
      </View>
      <View style={s.row}>
        <Text style={[s.status, { color }]}>{statusLabel[status]}</Text>
        {lot.over_capacity ? <Text style={s.badge}>Acima da capacidade</Text> : null}
      </View>
    </Pressable>
  )
}

export function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.kpi} accessible accessibilityLabel={`${label}: ${value}`}>
      <T variant="muted">{label}</T>
      <Text style={s.kpiValue}>{value}</Text>
    </View>
  )
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.surfaceAlt },
  content: { padding: 16, gap: 12 },
  title: { fontFamily: fonts.heading, fontSize: 22, color: colors.ink },
  subtitle: { fontFamily: fonts.headingSemi, fontSize: 17, color: colors.ink },
  body: { fontFamily: fonts.body, fontSize: 15, color: colors.ink },
  muted: { fontFamily: fonts.body, fontSize: 13, color: colors.muted },
  center: { textAlign: 'center' },
  footer: { paddingVertical: 16 },
  state: { padding: 24, alignItems: 'center', gap: 12 },
  button: {
    minHeight: TOUCH,
    minWidth: TOUCH,
    paddingHorizontal: 14,
    justifyContent: 'center',
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.primary,
    backgroundColor: colors.surface,
  },
  buttonSelected: { backgroundColor: colors.primary },
  buttonText: { fontFamily: fonts.bodyMedium, fontSize: 14, color: colors.primary },
  buttonTextSelected: { color: colors.surface },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'flex-start', borderWidth: 1, borderRadius: 16, paddingHorizontal: 10, paddingVertical: 4 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  chipText: { fontFamily: fonts.bodyMedium, fontSize: 13 },
  card: { backgroundColor: colors.surface, borderRadius: 12, padding: 16, gap: 8, borderWidth: 1, borderColor: colors.border, minHeight: TOUCH },
  row: { flexDirection: 'row', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 },
  big: { fontFamily: fonts.heading, fontSize: 32 },
  track: { height: 8, borderRadius: 4, backgroundColor: colors.border, overflow: 'hidden' },
  fill: { height: 8 },
  status: { fontFamily: fonts.bodyMedium, fontSize: 14 },
  badge: { fontFamily: fonts.bodyMedium, fontSize: 12, color: colors.surface, backgroundColor: colors.full, borderRadius: 8, paddingHorizontal: 8, paddingVertical: 2, overflow: 'hidden' },
  kpi: { backgroundColor: colors.surface, borderRadius: 10, padding: 12, minWidth: '45%', flexGrow: 1, borderWidth: 1, borderColor: colors.border },
  kpiValue: { fontFamily: fonts.heading, fontSize: 20, color: colors.ink },
})
