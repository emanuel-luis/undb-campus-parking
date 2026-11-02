import { Tabs } from 'expo-router'
import { Image } from 'react-native'
import logo from '../../assets/2026-06-logo-branca.png'
import { colors, fonts, headerOptions } from '../../src/theme'

const headerTitle = () => <Image source={logo} style={{ width: 110, height: 33 }} resizeMode="contain" accessibilityLabel="UNDB" />

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        ...headerOptions,
        headerTitle,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.muted,
        tabBarLabelStyle: { fontFamily: fonts.bodyMedium, fontSize: 14 },
        tabBarIcon: () => null,
        tabBarIconStyle: { display: 'none' },
      }}
    >
      <Tabs.Screen name="index" options={{ title: 'Vagas' }} />
      <Tabs.Screen name="relatorios" options={{ title: 'Relatórios' }} />
    </Tabs>
  )
}
