import { NavigationContainer } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import React from "react";
import { ActivityIndicator, View } from "react-native";

import type { RootStackParamList } from "./src/shell/navigation";
import { AppProvider, useApp } from "./src/shell/services";
import DeliveryDetailScreen from "./src/screens/DeliveryDetailScreen";
import HomeScreen from "./src/screens/HomeScreen";
import LoginScreen from "./src/screens/LoginScreen";
import NewDeliveryScreen from "./src/screens/NewDeliveryScreen";
import QueueScreen from "./src/screens/QueueScreen";

const Stack = createNativeStackNavigator<RootStackParamList>();

function Routes() {
  const { auth } = useApp();
  if (auth === "loading") {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator size="large" />
      </View>
    );
  }
  return (
    <NavigationContainer>
      <Stack.Navigator>
        {auth === "signed_out" ? (
          <Stack.Screen name="Login" component={LoginScreen} options={{ headerShown: false }} />
        ) : (
          <>
            <Stack.Screen name="Home" component={HomeScreen} options={{ headerShown: false }} />
            <Stack.Screen name="NewDelivery" component={NewDeliveryScreen} options={{ title: "New delivery" }} />
            <Stack.Screen name="DeliveryDetail" component={DeliveryDetailScreen} options={{ title: "Delivery" }} />
            <Stack.Screen name="Queue" component={QueueScreen} options={{ title: "Waiting to send" }} />
          </>
        )}
      </Stack.Navigator>
    </NavigationContainer>
  );
}

export default function App() {
  return (
    <AppProvider>
      <Routes />
    </AppProvider>
  );
}
