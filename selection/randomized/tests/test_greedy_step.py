import numpy as np
from scipy.stats import norm as ndist

import regreg.api as rr

from selection.randomized.api import randomization, multiple_views, pairs_bootstrap_glm, bootstrap_cov, glm_group_lasso, glm_greedy_step, pairs_inactive_score_glm

from selection.distributions.discrete_family import discrete_family
from selection.sampling.langevin import projected_langevin

from . import wait_for_return_value, logistic_instance

@wait_for_return_value
def test_overall_null_two_views():
    s, n, p = 5, 200, 20 

    randomizer = randomization.laplace((p,), scale=0.5)
    X, y, beta, _ = logistic_instance(n=n, p=p, s=s, rho=0.1, snr=7)

    nonzero = np.where(beta)[0]
    lam_frac = 1.

    loss = rr.glm.logistic(X, y)
    epsilon = 1.

    lam = lam_frac * np.mean(np.fabs(np.dot(X.T, np.random.binomial(1, 1. / 2, (n, 10000)))).max(0))
    W = np.ones(p)*lam
    W += np.arange(p) / 200
    W[0] = 0 # use at least some unpenalized
    penalty = rr.group_lasso(np.arange(p),
                             weights=dict(zip(np.arange(p), W)), lagrange=1.)
    # first randomization

    M_est1 = glm_group_lasso(loss, epsilon, penalty, randomizer)
    M_est1.solve()
    bootstrap_score1 = M_est1.setup_sampler()

    # second randomization -- a greedy step from LASSO

    active_groups = M_est1.active_groups + M_est1.unpenalized_groups
    inactive_groups = ~active_groups
    inactive_randomizer = randomization.laplace((inactive_groups.sum(),), scale=0.5)

    step = glm_greedy_step(loss, penalty,
                           active_groups,
                           inactive_groups,
                           inactive_randomizer)
    step.solve()
    bootstrap_score2 = step.setup_sampler()

    # we take target to be union of two active sets

    active = M_est1.overall + step.maximizing_variables

    if set(nonzero).issubset(np.nonzero(active)[0]):
        boot_target, target_observed = pairs_bootstrap_glm(loss, active)

        # target are all true null coefficients selected

        sampler = lambda : np.random.choice(n, size=(n,), replace=True)
        target_cov, cov1, cov2 = bootstrap_cov(sampler, boot_target, cross_terms=(bootstrap_score1, bootstrap_score2))

        active_set = np.nonzero(active)[0]
        inactive_selected = I = [i for i in np.arange(active_set.shape[0]) if active_set[i] not in nonzero]

        # is it enough only to bootstrap the inactive ones?
        # seems so...

        A1, b1 = M_est1.condition(cov1[I], target_cov[I][:,I], target_observed[I])
        A2, b2 = step.condition(cov2[I], target_cov[I][:,I], target_observed[I])

        target_inv_cov = np.linalg.inv(target_cov[I][:,I])

        initial_state = np.hstack([target_observed[I],
                                   M_est1.observed_opt_state,
                                   step.observed_opt_state])

        ntarget = len(I)
        target_slice = slice(0, ntarget)
        opt_slice1 = slice(ntarget, p + ntarget)
        opt_slice2 = slice(p + ntarget, 2*p + ntarget)

        def target_gradient(state):
            # with many samplers, we will add up the `target_slice` component
            # many target_grads
            # and only once do the Gaussian addition of full_grad

            target = state[target_slice]
            opt_state1 = state[opt_slice1]
            opt_state2 = state[opt_slice2]
            target_grad1 = M_est1.randomization_gradient(target, (A1, b1), opt_state1)
            target_grad2 = step.randomization_gradient(target, (A2, b2), opt_state2)

            full_grad = np.zeros_like(state)
            full_grad[opt_slice1] = -target_grad1[1]
            full_grad[opt_slice2] = -target_grad2[1]
            full_grad[target_slice] -= target_grad1[0] + target_grad2[0]
            full_grad[target_slice] -= target_inv_cov.dot(target)

            return full_grad

        def target_projection(state):
            opt_state1 = state[opt_slice1]
            state[opt_slice1] = M_est1.projection(opt_state1)
            opt_state2 = state[opt_slice2]
            state[opt_slice2] = step.projection(opt_state2)
            return state

        target_langevin = projected_langevin(initial_state,
                                             target_gradient,
                                             target_projection,
                                             .5 / (2*p + 1))


        Langevin_steps = 10000
        burning = 2000
        samples = []
        for i in range(Langevin_steps):
            target_langevin.next()
            if (i>=burning):
                samples.append(target_langevin.state[target_slice].copy())
                
        test_stat = lambda x: np.linalg.norm(x)
        observed = test_stat(target_observed[I])
        sample_test_stat = np.array([test_stat(x) for x in samples])

        family = discrete_family(sample_test_stat, np.ones_like(sample_test_stat))
        pval = family.ccdf(0, observed)
        return pval
