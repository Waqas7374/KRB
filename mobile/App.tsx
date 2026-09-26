import React from "react";
import { NavigationContainer } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import LoginScreen from "./screens/LoginScreen";
import TruckEntryScreen from "./screens/TruckEntryScreen";

export type RootStackParamList = {
  Login: undefined;
  TruckEntry: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();

export default function App() {
  return (
    <NavigationContainer>
      <Stack.Navigator initialRouteName="Login">
        <Stack.Screen
          name="Login"
          component={LoginScreen}
          options={{ headerShown: false }}
        />
        <Stack.Screen
          name="TruckEntry"
          component={TruckEntryScreen}
          options={{ title: "New truck entry" }}
        />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
