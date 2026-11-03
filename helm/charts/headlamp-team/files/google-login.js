(function () {
  "use strict";
  const { React, MuiMaterial, registerRoute } = window.pluginLib;
  const clusterName = {{ .Values.googleAuth.clusterName | toJson }};
  const clusterRoot = "/c/" + encodeURIComponent(clusterName) + "/";

  function GoogleSignIn() {
    return React.createElement(
      MuiMaterial.Box,
      { sx: { p: 3, maxWidth: 520, mx: "auto" } },
      React.createElement(MuiMaterial.Typography, { variant: "h5", gutterBottom: true }, "Google 로그인"),
      React.createElement(
        MuiMaterial.Typography,
        { sx: { mb: 2 } },
        "GKE를 보려면 허용된 Google 계정으로 로그인하세요. 로그인 후 이 클러스터 화면으로 돌아옵니다."
      ),
      React.createElement(
        MuiMaterial.Button,
        {
          component: "a",
          href: "/gke/oauth2/start?rd=" + encodeURIComponent(clusterRoot),
          variant: "contained"
        },
        "Google로 로그인"
      )
    );
  }

  // Exact custom routes precede Headlamp's default auth routes. AWS stays unchanged.
  for (const suffix of ["login", "token"]) {
    registerRoute({
      path: clusterRoot + suffix,
      exact: true,
      name: "hopcharlie-gke-google-" + suffix,
      useClusterURL: false,
      noAuthRequired: true,
      sidebar: null,
      component: GoogleSignIn
    });
  }
})();
