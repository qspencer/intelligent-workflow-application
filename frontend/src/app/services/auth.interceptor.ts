import { HttpInterceptorFn } from '@angular/common/http';

/**
 * Dev-mode auth: identify the user via X-Dev-User / X-Dev-Groups headers.
 * The values come from `localStorage`. In production (AUTH_MODE=oidc on the
 * backend), this interceptor is replaced with one that attaches a Bearer token
 * obtained from the IdP. Out of scope for Week 6.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const user = localStorage.getItem('wp.user') ?? 'dev-user';
  const groups = localStorage.getItem('wp.groups') ?? 'admins';
  const cloned = req.clone({
    setHeaders: {
      'X-Dev-User': user,
      'X-Dev-Groups': groups,
    },
  });
  return next(cloned);
};
