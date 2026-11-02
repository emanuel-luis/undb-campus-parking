// UNDB tokens (decision 49), the same values as the web app. Status colors are additions, not brand.
export const colors = {
  primary: '#CF004E',
  primaryStrong: '#770131',
  purple: '#7A1F5C',
  ink: '#1B0D17',
  muted: '#54595F',
  accent: '#FFBA00',
  surface: '#FFFFFF',
  surfaceAlt: '#F6F1F4',
  border: '#E3D6DE',
  free: '#1F7A4D',
  busy: '#9A5B00',
  full: '#B42318',
} as const

export const fonts = {
  heading: 'Montserrat_700Bold',
  headingSemi: 'Montserrat_600SemiBold',
  body: 'Roboto_400Regular',
  bodyMedium: 'Roboto_500Medium',
} as const

/** Minimum touch target, in points. */
export const TOUCH = 44

/** Navigation header: white on primary-strong. */
export const headerOptions = {
  headerStyle: { backgroundColor: colors.primaryStrong },
  headerTintColor: colors.surface,
  headerTitleStyle: { fontFamily: fonts.heading },
} as const
