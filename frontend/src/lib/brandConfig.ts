export type BrandConfig = {
  name: string;
  productName: string;
  shortName: string;
  tagline: string;
  logoPath: string;
  logoSmPath: string;
  metaTitle: string;
  metaDescription: string;
  metaAuthor: string;
  ogTitle: string;
  twitterSite: string;
  sidebarLogoAlt: string;
  welcomeTitle: string;
  settingsTitle: string;
  externalUrl: string;
  installDirectoryName: string;
  dockerComposeServiceName: string;
  documentationUrl: string;
  githubUrl: string;
  issueTrackerUrl: string;
  importDir: string;
  importDirEnvVar: string;
  docsConnectedModeUrl: string;
  devApiUrl: string;
  prodApiUrl: string;
};


export const AIM_DATA_BRAND: BrandConfig = {
  name: "AIM Data",
  productName: "AIM Data",
  shortName: "AD",
  tagline: "Connect your private data to ai.market",
  logoPath: "/ai-market-logo.svg",
  logoSmPath: "/aim-data-logo-sm.png",
  metaTitle: "AIM Data — ai.market",
  metaDescription: "AIM Data — Connect your private data to ai.market",
  metaAuthor: "AIM Data",
  ogTitle: "AIM Data",
  twitterSite: "@aidotmarket",
  sidebarLogoAlt: "ai.market",
  welcomeTitle: "Welcome to AIM Data",
  settingsTitle: "AIM Data",
  externalUrl: "https://ai.market",
  installDirectoryName: "aim-data",
  dockerComposeServiceName: "vectoraiz",
  documentationUrl: "https://ai.market/docs",
  githubUrl: "https://github.com/aidotmarket/aim-channel",
  issueTrackerUrl: "https://github.com/aidotmarket/aim-channel/issues",
  importDir: "~/aim-data-imports/",
  importDirEnvVar: "AIM_DATA_IMPORT_DIR",
  docsConnectedModeUrl: "https://ai.market/docs/aim-data/connected-mode",
  devApiUrl: "",
  prodApiUrl: "",
};


export function getActiveBrand(): BrandConfig {
  // AIM Data is its own product; no runtime brand switch (de-skinned S751)
  return AIM_DATA_BRAND;
}
