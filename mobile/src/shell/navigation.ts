export type RootStackParamList = {
  Login: undefined;
  Home: undefined;
  /** `correctId`: answering a reviewer's request. `fixOpId`: mending an entry the server refused. */
  NewDelivery: { correctId?: string; fixOpId?: string } | undefined;
  DeliveryDetail: { id: string };
  Queue: undefined;
};
