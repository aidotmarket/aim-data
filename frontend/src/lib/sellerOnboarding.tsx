import { ToastAction } from "@/components/ui/toast";
import { getActiveBrand } from "@/lib/brandConfig";

export const sellerSetupUrl = `${getActiveBrand().externalUrl}/dashboard`;

export function openSellerSetup() {
  window.open(sellerSetupUrl, "_blank", "noopener");
}

export function sellerSetupToastAction() {
  return (
    <ToastAction altText="Finish seller setup" onClick={openSellerSetup}>
      Finish setup
    </ToastAction>
  );
}

export const sellerSetupRequiredDescription =
  "Your listing is now LIVE but not purchasable until you finish setup: enable two-factor authentication, then connect Stripe for payouts.";
