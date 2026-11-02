import { Image, View } from 'react-native'
import logo from '../../assets/2024-11-logo.png'
import { Screen, T } from '../ui'

/** Shown when EXPO_PUBLIC_API_BASE_URL is not set: there is no default host to guess. */
export function MissingConfigScreen() {
  return (
    <Screen>
      <View style={{ gap: 12, paddingTop: 48, alignItems: 'flex-start' }}>
        <Image source={logo} style={{ width: 204, height: 62 }} accessibilityLabel="UNDB" />
        <T variant="title">Configure o endereço da API</T>
        <T>
          Defina EXPO_PUBLIC_API_BASE_URL com o endereço da API na sua rede local, por exemplo http://&lt;IP-da-LAN&gt;:8000,
          e reinicie o Expo (npx expo start).
        </T>
        <T variant="muted">No celular, localhost aponta para o próprio aparelho, por isso use o IP do computador que roda a API.</T>
      </View>
    </Screen>
  )
}
