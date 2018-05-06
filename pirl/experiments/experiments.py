import collections
import functools
import itertools
import joblib
import logging
import os
import os.path as osp

from joblib import Memory
import gym

from pirl import utils
from pirl.experiments import config

logger = logging.getLogger('pirl.experiments.experiments')
memory = Memory(cachedir=config.CACHE_DIR, verbose=0)

def make_irl_algo(algo):
    if algo in config.SINGLE_IRL_ALGORITHMS:
        return config.SINGLE_IRL_ALGORITHMS[algo]
    else:
        return config.POPULATION_IRL_ALGORITHMS[algo]


def sanitize_env_name(env_name):
    return env_name.replace('/', '_')

def _parallel_envs(env_name, parallel, base_seed, wrapper=None):
    def helper(i):
        env = gym.make(env_name)
        if wrapper is not None:
            env = wrapper(env)
        env.seed(base_seed + i)
        return env
    return [functools.partial(helper, i) for i in range(parallel)]

def __train_policy(rl, discount, env_name, parallel, seed, out_dir):
    gen_policy, _sample, compute_value = config.RL_ALGORITHMS[rl]
    log_dir = osp.join(out_dir, sanitize_env_name(env_name), rl)
    os.makedirs(log_dir)

    train_seed = utils.create_seed(seed + 'train')
    env_fns = _parallel_envs(env_name, parallel, train_seed)
    p = gen_policy(env_fns, discount=discount, log_dir=log_dir)
    joblib.dump(p, osp.join(log_dir, 'policy.pkl'))  # save for debugging

    eval_seed = utils.create_seed(seed + 'eval')
    env_fns = _parallel_envs(env_name, parallel, eval_seed)
    v = compute_value(env_fns, p, discount=1.00, seed=eval_seed)

    return p, v
# avoid name clash in pickling
_train_policy = memory.cache(ignore=['out_dir'])(__train_policy)


@memory.cache(ignore=['out_dir', 'video_every', 'policy'])
def synthetic_data(env_name, rl, num_trajectories, parallel, seed,
                   out_dir, video_every, policy):
    '''Precondition: policy produced by RL algorithm rl.'''
    _, sample, _ = config.RL_ALGORITHMS[rl]

    video_dir = osp.join(out_dir, sanitize_env_name(env_name), 'videos')
    if video_every is None:
        video_callable = lambda x: False
    else:
        video_callable = lambda x: x % video_every == 0
    def  monitor(env):
        return gym.wrappers.Monitor(env, video_dir,
                                    video_callable=video_callable, force=True)
    data_seed = utils.create_seed(seed + 'data')
    env_fns = _parallel_envs(env_name, parallel, data_seed, wrapper=monitor)
    samples = sample(env_fns, policy, num_trajectories, data_seed)
    #TODO: numpy array rather than Python list?
    return [(observations, actions) for (observations, actions, rewards) in samples]


@utils.log_errors
def _expert_trajs(env_name, experiment, rl_name, discount, parallel,
                  seed, num_trajectories, video_every, log_dir):
    logger.debug('%s: training %s on %s', experiment, rl_name, env_name)
    policy, value = _train_policy(rl_name, discount, env_name, parallel, seed, log_dir)

    logger.debug('%s: sampling from %s', experiment, env_name)
    _, sample, _ = config.RL_ALGORITHMS[rl_name]
    trajectories = synthetic_data(env_name, rl_name, num_trajectories, parallel,
                                  seed, log_dir, video_every, policy)

    return trajectories, value


def expert_trajs(experiment, out_dir, cfg, pool, video_every, seed):
    logger.debug('%s: generating synthetic data: training', experiment)
    log_dir = osp.join(out_dir, 'expert')
    os.makedirs(log_dir)
    parallel = cfg.get('parallel_rollouts', 1)
    max_trajectories = max(cfg['num_trajectories'])

    f = functools.partial(_expert_trajs, experiment=experiment,
                          rl_name=cfg['expert'], discount=cfg['discount'],
                          parallel=parallel, seed=seed,
                          num_trajectories=max_trajectories,
                          video_every=video_every, log_dir=log_dir)
    results = pool.map(f, cfg['environments'], chunksize=1)

    trajectories = collections.OrderedDict()
    values = collections.OrderedDict()
    for name, (traj, val) in zip(cfg['environments'], results):
        trajectories[name] = traj
        values[name] = val

    return trajectories, values

@utils.log_errors
def __run_population_irl(irl_name, n, m, small_env, experiment,
                         out_dir, env_names, parallel, trajectories, discount, seed):
    logger.debug('%s: running IRL algo: %s [%s=%s/%s]',
                 experiment, irl_name, small_env, m, n)
    irl_algo, _reward_wrapper, compute_value = config.POPULATION_IRL_ALGORITHMS[irl_name]
    subset = {k: v[:n] for k, v in trajectories.items()}
    log_root = osp.join(out_dir, 'irl', irl_name)
    if small_env is not None:
        subset[small_env] = subset[small_env][:m]
        log_dir = osp.join(log_root, sanitize_env_name(small_env), '{}:{}'.format(m, n))
    else:
        log_dir = osp.join(log_root, '{}'.format(n))
    os.makedirs(log_dir)

    irl_seed = utils.create_seed(seed + 'irl')
    env_fns = {k: _parallel_envs(k, parallel, irl_seed) for k in env_names}
    rewards, policies = irl_algo(env_fns, subset, discount=discount, log_dir=log_dir)

    # Save learnt reward & policy for debugging purposes
    joblib.dump(rewards, osp.join(log_dir, 'rewards.pkl'))
    joblib.dump(policies, osp.join(log_dir, 'policies.pkl'))

    eval_seed = utils.create_seed(seed + 'eval')
    env_fns = {k: _parallel_envs(k, parallel, eval_seed) for k in env_names}
    values = {k: compute_value(env_fns[k], p, discount=1.00, seed=eval_seed)
              for k, p in policies.items()}

    return rewards, values
_run_population_irl = memory.cache(ignore=['out_dir'])(__run_population_irl)

@utils.log_errors
def __run_single_irl(irl_name, n, env_name, parallel,
                     experiment, out_dir, trajectories, discount, seed):
    logger.debug('%s: running IRL algo: %s [%s]', experiment, irl_name, n)
    irl_algo, _reward_wrapper, compute_value = config.SINGLE_IRL_ALGORITHMS[irl_name]
    subset = trajectories[:n]
    log_dir = osp.join(out_dir, 'irl', irl_name, env_name, '{}'.format(n))
    os.makedirs(log_dir)

    irl_seed = utils.create_seed(seed + 'irl')
    env_fns = _parallel_envs(env_name, parallel, irl_seed)
    reward, policy = irl_algo(env_fns, subset, discount=discount, log_dir=log_dir)

    # Save learnt reward & policy for debugging purposes
    joblib.dump(reward, osp.join(log_dir, 'reward.pkl'))
    joblib.dump(policy, osp.join(log_dir, 'policy.pkl'))

    eval_seed = utils.create_seed(seed + 'eval')
    env_fns = _parallel_envs(env_name, parallel, eval_seed)
    value = compute_value(env_fns, policy, discount=1.00, seed=eval_seed)

    return reward, value
_run_single_irl = memory.cache(ignore=['out_dir'])(__run_single_irl)


def setdef(d, k):
    return d.setdefault(k, collections.OrderedDict())


def run_irl(experiment, out_dir, cfg, pool, trajectories, seed):
    '''Run experiment in parallel. Returns tuple (reward, value) where each are
       nested OrderedDicts, with key in the format:
        - IRL algo
        - Number of trajectories for other environments
        - Number of trajectories for this environment
        - Environment
       Note that for this experiment type, the second and third arguments are
       always the same.
    '''
    kwargs = {
        'experiment': experiment,
        'out_dir': out_dir,
        'parallel': cfg.get('parallel_rollouts', 1),
        'discount': cfg['discount'],
        'seed': seed,
    }
    res = collections.OrderedDict()
    for irl_name, n in itertools.product(cfg['irl'], sorted(cfg['num_trajectories'])):
        kwds = kwargs.copy()
        kwds.update({'irl_name': irl_name, 'n': n})
        if irl_name in config.SINGLE_IRL_ALGORITHMS:
            for env in cfg['environments']:
                env_kwds = kwds.copy()
                env_kwds.update({
                    'env_name': env,
                    'trajectories': trajectories[env],
                })
                delayed = pool.apply_async(_run_single_irl, kwds=env_kwds)
                setdef(setdef(setdef(res, irl_name), n), n)[env] = delayed
        elif irl_name in config.POPULATION_IRL_ALGORITHMS:
            kwds.update({
                'env_names': cfg['environments'],
                'trajectories': trajectories,
                'm': None,
                'small_env': None,
            })
            delayed = pool.apply_async(_run_population_irl, kwds=kwds)
            setdef(setdef(res, irl_name), n)[n] = delayed
        else:
            assert False

    rewards = utils.nested_async_get(res, lambda x: x[0])
    values = utils.nested_async_get(res, lambda x: x[1])

    return rewards, values


def run_few_shot_irl(experiment, out_dir, cfg, pool, trajectories, seed):
    '''Same spec as run_irl.'''
    kwargs = {
        'experiment': experiment,
        'out_dir': out_dir,
        'parallel': cfg.get('parallel_rollouts', 1),
        'discount': cfg['discount'],
        'seed': seed,
    }

    sin_args = [cfg['irl'], sorted(cfg['few_shot']), cfg['environments']]
    sin_res = {}
    for irl_name, m, env in itertools.product(*sin_args):
        if irl_name in config.SINGLE_IRL_ALGORITHMS:
            kwds = kwargs.copy()
            kwds.update({
                'irl_name': irl_name,
                'n': m,
                'env_name': env,
                'trajectories': trajectories[env],
            })
            delayed = pool.apply_async(_run_single_irl, kwds=kwds)
            setdef(setdef(sin_res, irl_name), m)[env] = delayed

    num_traj = sorted(cfg['num_trajectories'])
    pop_res = {}
    for n, irl_name, m, env in itertools.product(num_traj, *sin_args):
        if irl_name in config.POPULATION_IRL_ALGORITHMS:
            kwds = kwargs.copy()
            kwds.update({
                'irl_name': irl_name,
                'n': n,
                'm': m,
                'env_names': cfg['environments'],
                'trajectories': trajectories,
                'small_env': env,
            })
            delayed = pool.apply_async(_run_population_irl, kwds=kwds)
            setdef(setdef(setdef(pop_res, irl_name), n), m)[env] = delayed

    rewards = collections.OrderedDict()
    values = collections.OrderedDict()

    sin_res = utils.nested_async_get(sin_res)
    for irl_name, d in sin_res.items():
        for m, d2 in d.items():
            for env, (r, v) in d2.items():
                for n in num_traj:
                    if n > m:
                        break
                    setdef(setdef(setdef(rewards, irl_name), m), m)[env] = r
                    setdef(setdef(setdef(values, irl_name), m), m)[env] = v

    pop_res = utils.nested_async_get(pop_res)
    for irl_name, d in pop_res.items():
        for n, d2 in d.items():
            for m, d3 in d2.items():
                for env, (r, v) in d3.items():
                    setdef(setdef(setdef(rewards, irl_name), n), m)[env] = r[env]
                    setdef(setdef(setdef(values, irl_name), n), m)[env] = v[env]

    return rewards, values


@utils.log_errors
def _value(experiment, irl_name, rl_name, env_name, parallel,
           log_dir, reward, discount, seed):
    logger.debug('%s: evaluating %s on %s (writing to %s)',
                 experiment, irl_name, env_name, log_dir)
    gen_policy, _sample, compute_value = config.RL_ALGORITHMS[rl_name]

    _, reward_wrapper, _ = make_irl_algo(irl_name)
    rw = functools.partial(reward_wrapper, new_reward=reward)

    train_seed = utils.create_seed(seed + 'eval_train')
    env_fns = _parallel_envs(env_name, parallel, train_seed, wrapper=rw)
    p = gen_policy(env_fns, discount=discount, log_dir=log_dir)

    logger.debug('%s: reoptimized %s on %s, sampling to estimate value',
                 experiment, irl_name, env_name)
    eval_seed = utils.create_seed(seed + 'eval_eval')
    env_fns = _parallel_envs(env_name, parallel, eval_seed)
    v = compute_value(env_fns, p, discount=1.00, seed=eval_seed)

    return v


def value(experiment, out_dir, cfg, pool, rewards, seed):
    '''
    Compute the expected value of (a) policies optimized on inferred reward,
    and (b) optimal policies for the ground truth reward. Policies will be
    computed using each RL algorithm specified in cfg['eval'].

    Args:
        - experiment
        - cfg: config.EXPERIMENTS[experiment]
        - rewards
    Returns:
        tuple, (value, ground_truth) where each is a nested dictionary of the
        same shape as rewards, with the leaf being a dictionary mapping from
        an RL algorithm in cfg['eval'] to a scalar value.
    '''
    discount = cfg['discount']
    parallel = cfg.get('parallel_rollouts', 1)
    value = collections.OrderedDict()
    ground_truth = {}
    for rl in cfg['eval']:
        for irl_name, reward_by_size in rewards.items():
            res_by_n = collections.OrderedDict()
            for n, reward_by_small_size in reward_by_size.items():
                res_by_m = collections.OrderedDict()
                for m, reward_by_env in reward_by_small_size.items():
                    res_by_env = {}
                    for env_name,r  in reward_by_env.items():
                        log_dir = osp.join(out_dir, 'eval',
                                           sanitize_env_name(env_name),
                                           '{}:{}:{}'.format(irl_name, m, n),
                                           rl)
                        args = (experiment, irl_name, rl, env_name, parallel,
                                log_dir, r, discount, seed)
                        delayed = pool.apply_async(_value, args)
                        res_by_env.setdefault(env_name, {})[rl] = delayed
                    res_by_m[m] = res_by_env
                res_by_n[n] = res_by_m
            value[irl_name] = res_by_n

        log_dir = osp.join(out_dir, 'eval', 'gt')
        for env_name in cfg['environments']:
            args = (rl, discount, env_name, parallel, seed, log_dir)
            delayed = pool.apply_async(_train_policy, args)
            ground_truth.setdefault(env_name, {})[rl] = delayed

    value = utils.nested_async_get(value)
    ground_truth = utils.nested_async_get(ground_truth, lambda x: x[1])

    return value, ground_truth


def run_experiment(experiment, pool, out_dir, video_every, seed):
    '''Run experiment defined in config.EXPERIMENTS.

    Args:
        - experiment(str): experiment name.
        - pool(multiprocessing.Pool)
        - out_dir(str): path to write logs and results to.
        - video_every(optional[int]): if None, do not record video.
        - seed(int)

    Returns:
        dict with key-value pairs:

        - trajectories: synthetic data.
            dict, keyed by environments, with values generated by synthetic_data.
        - rewards: IRL inferred reward.
            nested dict, keyed by environment then IRL algorithm.
        - value: value obtained reoptimizing in the environment.
            Use the RL algorithm used to generate the original synthetic data
            to train a policy on the inferred reward, then compute expected
            discounted value obtained from the resulting policy.
        - ground_truth: value obtained from RL policy.
        - info: info dict from IRL algorithms.
        '''
    utils.random_seed(seed)
    cfg = config.EXPERIMENTS[experiment]

    # Generate synthetic data
    trajs, expert_vals = expert_trajs(experiment, out_dir, cfg, pool,
                                      video_every, seed)

    # Run IRL
    fn = run_few_shot_irl if 'few_shot' in cfg else run_irl
    rewards, irl_values = fn(experiment, out_dir, cfg, pool, trajs, seed)

    # Evaluate IRL by reoptimizing in cfg['evals']
    values, ground_truth = value(experiment, out_dir, cfg, pool, rewards, seed)

    # Add in the value obtained by the expert policy & IRL policy
    for name, val in expert_vals.items():
        ground_truth.setdefault(name, collections.OrderedDict())['expert'] = val
    for irl_name, d1 in irl_values.items():
        for n, d2 in d1.items():
            for m, d3 in d2.items():
                for env, val in d3.items():
                    setdef(setdef(setdef(setdef(values, irl_name), n), m), env)['irl'] = val

    return {
        'trajectories': trajs,
        'rewards': rewards,
        'values': values,
        'ground_truth': expert_vals,
    }
