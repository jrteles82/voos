export type TripType = "oneway" | "roundtrip";

export interface RouteQuery {
  origin: string;
  destination: string;
  outboundDate: string;
  inboundDate: string;
  tripType: TripType;
}

export interface FlightResult {
  site: string;
  origin: string;
  destination: string;
  outboundDate: string;
  inboundDate: string;
  tripType: TripType;
  price: number | null;
  currency: string;
  url: string;
  notes: string;
  bestVendor: string;
  bestVendorPrice: number | null;
  bookingOptionsJson: string;
  screenshotPath?: string;
}

export interface ScanRow {
  origin: string;
  destination: string;
  outbound_date: string;
  inbound_date: string;
  trip_type: TripType;
  price: number | null;
  price_fmt: string;
  site: string;
  currency: string;
  url: string;
  notes: string;
  price_band: string;
  best_vendor: string;
  best_vendor_price: number | null;
  final_price_source: string;
  created_at?: string;
  booking_options_json?: string;
  screenshot_path?: string;
}
