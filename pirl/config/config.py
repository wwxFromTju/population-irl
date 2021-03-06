import functools
import itertools
import os.path as osp

import tensorflow as tf
from airl import envs  # used for side-effects (register Gym environments)

from pirl import agents, irl
from pirl.config.types import RLAlgorithm, IRLAlgorithm, MetaIRLAlgorithm

# Overrideable defaults
PROJECT_DIR = osp.dirname(osp.dirname(osp.dirname(osp.realpath(__file__))))
DATA_DIR = osp.join(PROJECT_DIR, 'data')
RAY_SERVER = None # Scheduler IP

try:
    from pirl.config.config_local import *
except ImportError:
    pass

# Directory locations

EXPERIMENTS_DIR = osp.join(DATA_DIR, 'experiments')
OBJECT_DIR = osp.join(DATA_DIR, 'objects')
CACHE_DIR = osp.join(DATA_DIR, 'cache')

# ML Framework Config

def make_tf_config():
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    return config
TENSORFLOW = make_tf_config()

# Logging
LOG_CFG = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard': {
                'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            },
        },
        'handlers': {
            'stream': {
                'level': 'DEBUG',
                'formatter': 'standard',
                'class': 'logging.StreamHandler',
            },
        },
        'loggers': {
            '': {
                'handlers': ['stream'],
                'level': 'DEBUG',
                'propagate': True
            },
        }
    }

# RL Algorithms

RL_ALGORITHMS = {
    'value_iteration': RLAlgorithm(
        train=agents.tabular.policy_env_wrapper(agents.tabular.q_iteration_policy),
        sample=agents.tabular.sample,
        value=agents.tabular.value_in_mdp,
        vectorized=False,
        uses_gpu=False,
    ),
    'max_ent': RLAlgorithm(
        train=agents.tabular.policy_env_wrapper(irl.tabular_maxent.max_ent_policy),
        sample=agents.tabular.sample,
        value=agents.tabular.value_in_mdp,
        vectorized=False,
        uses_gpu=False,
    ),
    'max_causal_ent': RLAlgorithm(
        train=agents.tabular.policy_env_wrapper(irl.tabular_maxent.max_causal_ent_policy),
        sample=agents.tabular.sample,
        value=agents.tabular.value_in_mdp,
        vectorized=False,
        uses_gpu=False,
    ),
}

def ppo_cts_pol(num_timesteps):
    train = functools.partial(agents.ppo.train_continuous,
                              tf_config=TENSORFLOW,
                              num_timesteps=num_timesteps)
    sample = functools.partial(agents.ppo.sample, tf_config=TENSORFLOW)
    value = functools.partial(agents.sample.value, sample)
    return RLAlgorithm(train=train,
                       sample=sample,
                       value=value,
                       vectorized=True,
                       uses_gpu=True)
RL_ALGORITHMS['ppo_cts'] = ppo_cts_pol(1e6)
RL_ALGORITHMS['ppo_cts_500k'] = ppo_cts_pol(5e5)
RL_ALGORITHMS['ppo_cts_200k'] = ppo_cts_pol(2e5)
RL_ALGORITHMS['ppo_cts_short'] = ppo_cts_pol(1e5)
RL_ALGORITHMS['ppo_cts_shortest'] = ppo_cts_pol(1e4)

# IRL Algorithms

## Single environment IRL algorithms (not population)

SINGLE_IRL_ALGORITHMS = {
    # Maximum Causal Entropy (Ziebart 2010)
    'mce': IRLAlgorithm(
        train=irl.tabular_maxent.irl,
        reward_wrapper=agents.tabular.TabularRewardWrapper,
        sample=agents.tabular.sample,
        value=agents.tabular.value_in_mdp,
        vectorized=False,
        uses_gpu=False,
    ),
    'mce_shortest': IRLAlgorithm(
        train=functools.partial(irl.tabular_maxent.irl, num_iter=500),
        reward_wrapper=agents.tabular.TabularRewardWrapper,
        sample=agents.tabular.sample,
        value=agents.tabular.value_in_mdp,
        vectorized=False,
        uses_gpu=False,
    ),
    # Maximum Entropy (Ziebart 2008)
    'me': IRLAlgorithm(
        train=functools.partial(irl.tabular_maxent.irl,
                                planner=irl.tabular_maxent.max_ent_policy),
        reward_wrapper=agents.tabular.TabularRewardWrapper,
        sample=agents.tabular.sample,
        value=agents.tabular.value_in_mdp,
        vectorized=False,
        uses_gpu=False,
    ),
}

from airl.models.imitation_learning import AIRLStateAction
AIRL_ALGORITHMS = {
    'so': dict(),
    'so_ent': dict(training_cfg={'entropy_weight': 1.0}),
    'sa': dict(model_cfg={'model': irl.airl.AIRLStateAction, 'max_itrs': 10}),
    'sa_ent': dict(model_cfg={'model': irl.airl.AIRLStateAction, 'max_itrs': 10},
                   training_cfg={'entropy_weight': 1.0}),
    'random': dict(policy_cfg={'policy': irl.airl.GaussianPolicy}),
    # parameters to match scripts/pendulum_irl.py from adversarial-irl
    'orig_pendulum': {
        'model_cfg': {
            'model': irl.airl.AIRLStateAction,
            'max_itrs': 100,
        },
        'training_cfg': {
            'n_itr': 200,
            'batch_size': 1000,
            'discrim_train_itrs': 50,
        }
    },
}

AIRL_ITERATIONS = {None: 1000,
                   'short': 100,
                   'shorter': 50,
                   'shortest': 25,
                   'dummy': 2}
airl_reward = functools.partial(irl.airl.airl_reward_wrapper, tf_cfg=TENSORFLOW)
airl_sample = functools.partial(irl.airl.sample, tf_cfg=TENSORFLOW)
airl_value = functools.partial(agents.sample.value, airl_sample)
for k, kwargs in AIRL_ALGORITHMS.items():
    for k2, v2 in AIRL_ITERATIONS.items():
        name = 'airl_{}'.format(k)
        if k2 is not None:
            name = '{}_{}'.format(name, k2)

        kwds = dict(kwargs)
        if v2 is not None:
            training_cfg = dict(kwds.get('training_cfg', dict()))
            training_cfg['n_itr'] = v2
            kwds['training_cfg'] = training_cfg

        train = functools.partial(irl.airl.irl, tf_cfg=TENSORFLOW, **kwds)
        SINGLE_IRL_ALGORITHMS[name] = IRLAlgorithm(
            train=train,
            reward_wrapper=airl_reward,
            sample=airl_sample,
            value=airl_value,
            vectorized=True,
            uses_gpu=True,
        )

gail_train = functools.partial(irl.gail.irl, tf_cfg=TENSORFLOW)
gail_sample = functools.partial(irl.gail.sample, tf_cfg=TENSORFLOW)
#TODO: gail default is 5e6, so check 1e6 doesn't hurt performance
for k, max_it in {'': 5e6, '_short': 1e6, '_shortest': 1e4}.items():
    train = functools.partial(gail_train, train_cfg={'max_timesteps': max_it})
    SINGLE_IRL_ALGORITHMS['gail' + k] = IRLAlgorithm(
        train=train,
        reward_wrapper=None,
        sample=gail_sample,
        value=functools.partial(agents.sample.value, gail_sample),
        vectorized=False,
        uses_gpu=True,
    )

## Population IRL algorithms

POPULATION_IRL_ALGORITHMS = dict()
def pop_maxent(**kwargs):
    return MetaIRLAlgorithm(
        metalearn=functools.partial(irl.tabular_maxent.metalearn, **kwargs),
        finetune=functools.partial(irl.tabular_maxent.finetune, **kwargs),
        reward_wrapper=agents.tabular.TabularRewardWrapper,
        sample=agents.tabular.sample,
        value=agents.tabular.value_in_mdp,
        vectorized=False,
        uses_gpu=False,
    )
for reg in range(-4,3):
    algo = pop_maxent(regularize=10**reg)
    POPULATION_IRL_ALGORITHMS['mcep_reg1e{}'.format(reg)] = algo
POPULATION_IRL_ALGORITHMS['mcep_reg0'] = pop_maxent(regularize=0)
POPULATION_IRL_ALGORITHMS['mcep_shortest_reg0'] = pop_maxent(regularize=0,
                                                             num_iter=500)

AIRLP_ALGORITHMS = {
    # 3-tuple with elements:
    # - common
    # - metalearn only
    # - finetune only
    'random': (dict(),
               dict(policy_per_task=False, policy_cfg={'policy': irl.airl.GaussianPolicy}),
               dict()),
    'so_joint': (dict(), dict(policy_per_task=False), dict()),
    'so_separate': (dict(), dict(policy_per_task=True), dict()),
    'so_separate_long_inner': (dict(),
                               dict(training_cfg={'n_itr': 50},
                                    policy_per_task=True),
                               dict()),
}

for k, (common, meta, fine) in AIRLP_ALGORITHMS.items():
    for k2, it in AIRL_ITERATIONS.items():
        for lr in [None] + list(range(1,4)):
            meta = dict(meta, **common)
            fine = dict(fine, **common)

            meta['outer_itr'] = it
            if lr is not None:
                meta['lr'] = 10 ** (-lr)
            if k2 == 'dummy':
                training_cfg = dict(fine.get('training_cfg', dict()))
                training_cfg['n_itr'] = 2
                meta['training_cfg'] = training_cfg

            fine['pol_itr'] = it // 4
            fine['irl_itr'] = it // 4

            metalearn_fn = functools.partial(irl.airl.metalearn,
                                             tf_cfg=TENSORFLOW, **meta)
            finetune_fn = functools.partial(irl.airl.finetune,
                                            tf_cfg=TENSORFLOW, **fine)
            entry = MetaIRLAlgorithm(metalearn=metalearn_fn,
                                     finetune=finetune_fn,
                                     reward_wrapper=airl_reward,
                                     sample=airl_sample,
                                     value=airl_value,
                                     vectorized=True,
                                     uses_gpu=True)
            algo_name = 'airlp_{}'.format(k)
            if k2 is not None:
                algo_name += '_' + k2
            if lr is not None:
                algo_name += '_lr1e-{}'.format(lr)
            POPULATION_IRL_ALGORITHMS[algo_name] = entry

def traditional_to_concat(singleirl):
    def metalearner(envs, trajectories, discount, seed, log_dir):
        return list(itertools.chain(*trajectories.values()))
    @functools.wraps(singleirl.train)
    def finetune(train_trajectories, envs, test_trajectories, discount, seed, **kwargs):
        concat_trajectories = train_trajectories + test_trajectories
        return singleirl.train(envs, concat_trajectories, discount, seed, **kwargs)
    return MetaIRLAlgorithm(metalearn=metalearner,
                            finetune=finetune,
                            reward_wrapper=singleirl.reward_wrapper,
                            sample=singleirl.sample,
                            value=singleirl.value,
                            vectorized=singleirl.vectorized,
                            uses_gpu=singleirl.uses_gpu)

for name, algo in SINGLE_IRL_ALGORITHMS.items():
    POPULATION_IRL_ALGORITHMS[name + 'c'] = traditional_to_concat(algo)

# Experiments

EXPERIMENTS = {}

# ONLY FOR TESTING CODE! Not real experiments.
EXPERIMENTS['dummy-test'] = {
    'environments': ['pirl/GridWorld-Simple-v0'],
    'discount': 1.00,
    'expert': 'value_iteration',
    'eval': ['value_iteration'],
    'irl': ['mce_shortest', 'mcep_shortest_reg0'],
    'train_trajectories': [20, 10],
    'test_trajectories': [20, 10],
    'seeds': 2,
}
EXPERIMENTS['few-dummy-test'] = {
    'train_environments': ['pirl/GridWorld-Simple-v0'],
    'test_environments': ['pirl/GridWorld-Simple-Deterministic-v0'],
    'discount': 1.00,
    'expert': 'value_iteration',
    'eval': ['value_iteration'],
    'irl': ['mce_shortest', 'mce_shortestc', 'mcep_shortest_reg0'],
    'train_trajectories': [20, 10],
    'test_trajectories': [0, 1, 5],
    'seeds': 2,
}
EXPERIMENTS['dummy-test-deterministic'] = {
    'environments': ['pirl/GridWorld-Simple-Deterministic-v0'],
    'discount': 1.00,
    'expert': 'value_iteration',
    'eval': ['value_iteration'],
    'irl': ['mce_shortest', 'mcep_shortest_reg0'],
    'train_trajectories': [20, 10],
    'test_trajectories': [20, 10],
    'seeds': 2,
}
EXPERIMENTS['dummy-continuous-test'] = {
    'environments': ['Reacher-v2'],
    'expert': 'ppo_cts_shortest',
    'eval': ['ppo_cts_shortest'],
    'irl': ['gail_shortest', 'airl_so_dummy', 'airl_random_dummy'],
    'test_trajectories': [10, 20],
    'seeds': 2,
}
EXPERIMENTS['few-dummy-continuous-test'] = {
    'train_environments': ['pirl/ReacherGoal-seed{}-0.1-v0'.format(i)
                           for i in range(0, 2)],
    'test_environments': ['pirl/ReacherGoal-seed{}-0.1-v0'.format(i)
                          for i in range(1, 3)],
    'expert': 'ppo_cts_shortest',
    'eval': ['ppo_cts_shortest'],
    'irl': ['airl_so_dummy',
            'airlp_so_joint_dummy',
            'airlp_so_separate_dummy',
            'airlp_random_dummy'],
    'train_trajectories': [10, 20],
    'test_trajectories': [10, 20],
    'seeds': 1,
}
EXPERIMENTS['dummy-continuous-test-medium'] = {
    'environments': ['Reacher-v2'],
    'expert': 'ppo_cts_short',
    'eval': ['ppo_cts_short'],
    'irl': ['airl_so'],
    'test_trajectories': [10, 100, 1000],
}
EXPERIMENTS['dummy-continuous-test-slow'] = {
    'environments': ['Reacher-v2'],
    'expert': 'ppo_cts',
    'eval': ['ppo_cts'],
    'irl': ['airl_so'],
    'test_trajectories': [10, 100, 1000],
}

# Test different planner combinations
EXPERIMENTS['unexpected-optimal'] = {
    'environments': ['pirl/GridWorld-Jungle-4x4-Soda-v0'],
    'discount': 1.00,
    'expert': 'max_causal_ent',
    'eval': ['value_iteration'],
    'irl': [
        'mce',
        'me',
    ],
    'test_trajectories': [200],
}

# Few-shot learning in gridworlds
jungle_types = ['Soda', 'Water', 'Liquid']
for shape in ['9x9', '4x4']:
    for few_shot in jungle_types:
        EXPERIMENTS['few-jungle-{}-{}'.format(shape, few_shot)] = {
            'train_environments': ['pirl/GridWorld-Jungle-{}-{}-v0'.format(shape, k)
                                   for k in jungle_types if k != few_shot],
            'test_environments': ['pirl/GridWorld-Jungle-{}-{}-v0'.format(shape, few_shot)],
            'discount': 1.00,
            'expert': 'max_causal_ent',
            'eval': ['value_iteration'],
            'irl': [
                'mce',
                'mcec',
                'mcep_reg0',
                'mcep_reg1e-4',
                'mcep_reg1e-3',
                'mcep_reg1e-2',
                'mcep_reg1e-1',
                'mcep_reg1e0',
            ],
            'train_trajectories': [1000],
            'test_trajectories': [0, 1, 2, 5, 10, 20, 50, 100],
        }
EXPERIMENTS['few-jungle-quick-tmp'] = {
    'train_environments': ['pirl/GridWorld-Jungle-9x9-{}-v0'.format(k)
                           for k in jungle_types if k != 'Water'],
    'test_environments': ['pirl/GridWorld-Jungle-9x9-Water-v0'],
    'discount': 1.00,
    'expert': 'max_causal_ent',
    'eval': ['value_iteration'],
    'irl': [
        'mcep_reg1e-1',
    ],
    'train_trajectories': [1000],
    'test_trajectories': [0, 1, 2, 5, 10, 20, 50, 100],
}

# Baselines for continuous control
EXPERIMENTS['continuous-baselines-classic'] = {
    # continuous state space but (mostly) discrete action spaces
    'environments': [
        'MountainCarContinuous-v0',
        'Pendulum-v0',
        # below are discrete which AIRL cannot currently work with
        # 'Acrobot-v1',
        # 'CartPole-v1',
        # 'MountainCar-v0',
    ],
    'expert': 'ppo_cts_short',
    'eval': ['ppo_cts_short'],
    'irl': ['gail_short',
            'airl_so_short',
            'airl_sa_short',
            'airl_random_short'],
    'test_trajectories': [1000],
}
EXPERIMENTS['debug-pendulum'] = {
    'environments': ['Pendulum-v0'],
    'expert': 'ppo_cts',
    'discount': 0.95,
    'irl': [],
    'test_trajectories': [1000],
    'eval': ['ppo_cts'],
}
EXPERIMENTS['continuous-baselines-easy'] = {
    'environments': [
        'Reacher-v2',
        'InvertedPendulum-v2',
        'InvertedDoublePendulum-v2'
    ],
    'expert': 'ppo_cts',
    'eval': ['ppo_cts'],
    'irl': ['gail', 'airl_so', 'airl_sa', 'airl_random'],
    'test_trajectories': [1000],
}
EXPERIMENTS['continuous-baselines-medium'] = {
    'environments': [
        'Swimmer-v2',
        'Hopper-v2',
        'HalfCheetah-v2',
    ],
    'expert': 'ppo_cts',
    'eval': ['ppo_cts'],
    'irl': ['gail', 'airl_so', 'airl_sa', 'airl_random'],
    'test_trajectories': [1000],
}
# Designed to closely match tests from adversarial-irl repository
# Differences: expert is PPO rather than TRPO, parallel rollouts,
# and number of trajectories (I've tried to match it closely)
EXPERIMENTS['airl-baselines-pendulum'] = {
    'environments': ['Pendulum-v0'],
    'expert': 'ppo_cts',
    'irl': ['airl_orig_pendulum'],
    'eval': ['ppo_cts'],
    # In scripts/pendulum_irl.py, loads 5 iterations * 1000 batch size = 5000 steps.
    # Episode is at most 100 steps long, so this corresponds to 500 trajectories.for
    # (This is quite a lot for such a simple task.)
    'test_trajectories': [500],
}
EXPERIMENTS['airl-baselines-ant'] = {
    'environments': ['airl/CustomAnt-v0'],
    'expert': 'ppo_cts',
    'irl': ['airl_so', 'airl_sa', 'airl_random'],
    'eval': ['ppo_cts'],
    # scripts/ant_irl.py loads 2 iterations * 4 runs * 20000 batch size
    # = 160,000 steps. Episode is at most 500 steps long, so 320 trajectories.
    'test_trajectories': [320],
}

# Continuous control
EXPERIMENTS['billiards'] = {
    'environments': ['pirl/Billiards{}-seed{}-v0'.format(n, i)
                     for n in [2,3,4] for i in range(1)],
    'expert': 'ppo_cts',
    'eval': [],
    'irl': ['airl_so'],
    'test_trajectories': [1000],
}
EXPERIMENTS['mountain-car-single'] = {
    'environments': ['pirl/MountainCarContinuous-2-left-0-0.05-v0'],
    # simple environment, small number of iterations sufficient to converge
    'expert': 'ppo_cts_short',
    'eval': ['ppo_cts_short'],
    'irl': ['airl_so_short', 'airl_so_ent_short', 'airl_so_shorter',
            'airl_sa_short', 'airl_sa_ent_short', 'airl_sa_shorter',
            'airl_random_short', 'airl_sa_shorter'],
    'test_trajectories': [1, 2, 5, 100],
}
EXPERIMENTS['mountain-car-vel'] = {
    'environments': ['pirl/MountainCarContinuous-2-left-{}-{}-v0'.format(vel, initial_noise)
                     for vel in [0, 0.1, 0.5, 1]
                     for initial_noise in [0.05, 0.1, 0.25]
                    ],
    # simple environment, small number of iterations sufficient to converge
    'expert': 'ppo_cts_short',
    'eval': ['ppo_cts_short'],
    'irl': ['airl_so_short', 'airl_sa_short'],
    'test_trajectories': [1, 2, 5, 100],
}
EXPERIMENTS['mountain-car-numpeaks'] = {
    'environments': ['pirl/MountainCarContinuous-{}-left-0-0.05-v0'.format(n)
                     for n in [2, 3, 4]],
    'expert': 'ppo_cts_short',
    'eval': ['ppo_cts_short'],
    'irl': ['airl_so_short', 'airl_sa_short', 'airl_random_short'],
    'test_trajectories': [1, 2, 5, 100],
}
EXPERIMENTS['mountain-car-multigoals'] = {
    'environments': ['pirl/MountainCarContinuous-{}-red-0-0.05-v0'.format(n)
                     for n in [2, 3, 4]],
    'expert': 'ppo_cts_short',
    'eval': ['ppo_cts_short'],
    'irl': ['airl_so_short', 'airl_sa_short', 'airl_random_short'],
    'test_trajectories': [1, 2, 5, 100],
}
EXPERIMENTS['reacher-multigoals'] = {
    'environments': ['pirl/ReacherGoal-seed{}-{}-v0'.format(seed, noise)
                     for seed in range(0,3) for noise in [0.1, 0.5, 1.0]],
    # simple environment, small number of iterations sufficient to converge
    'expert': 'ppo_cts_200k',
    'irl': ['airl_so_short', 'airl_sa_short', 'airl_random_short'],
    'eval': ['ppo_cts_200k'],
    'test_trajectories': [1, 2, 5, 100],
}
EXPERIMENTS['reacher-wall'] = {
    'environments': ['pirl/ReacherWall-{}-50-1.0-v0'.format(seed)
                     for seed in ['seed{}'.format(seed) for seed in [0, 1, 7]] + ['nowall']
                    ],
    # simple environment, small number of iterations sufficient to converge
    'expert': 'ppo_cts',
    'irl': ['airl_so_short', 'airl_sa_short', 'airl_random_short'],
    'eval': ['ppo_cts'],
    'test_trajectories': [1, 2, 5, 100],
}
EXPERIMENTS['reacher-wall-verification'] = {
    'environments': ['Reacher-v2'] +
                    ['pirl/ReacherWall-{}-50-{}-v0'.format(seed, sv)
                     for seed in ['nowall', 'seed1'] for sv in [0.1, 0.5, 1.0]],
    'expert': 'ppo_cts',
    'irl': [],
    'eval': [],
    'test_trajectories': [1, 2, 5, 100],
}

# Few-shot continuous control
EXPERIMENTS['mountain-car-numpeaks-metalearn'] = {
    'environments': ['pirl/MountainCarContinuous-{}-left-0-0.05-v0'.format(n)
                     for n in [2, 3, 4]],
    'expert': 'ppo_cts_short',
    'eval': ['ppo_cts_short'],
    'irl': ['airlp_random_short'],
    'train_trajectories': [100],
    'test_trajectories': [1, 100],
}
EXPERIMENTS['mountain-car-side-metalearn'] = {
    'environments': ['pirl/MountainCarContinuous-2-{}-target-0-0.05-v0'.format(side)
                     for side in ['left', 'right']],
    'expert': 'ppo_cts_short',
    'eval': ['ppo_cts_short'],
    'irl': ['airl_random_short',
            'airl_so_short',
            'airlp_random_short',
            'airlp_so_separate_short'],
    'train_trajectories': [100],
    'test_trajectories': [1, 2, 5, 100],
    'seeds': 10,
}
EXPERIMENTS['mountain-car-color-metalearn'] = {
    'environments': ['pirl/MountainCarContinuous-2-{}-0-0.05-v0'.format(col)
                     for col in ['red', 'blue']],
    'expert': 'ppo_cts_short',
    'eval': ['ppo_cts_short'],
    'irl': ['airl_random_short',
            'airl_sa_short',
            'airl_so_short',
            'airlp_random_short',
            'airlp_so_separate_short'],
    'train_trajectories': [100],
    'test_trajectories': [1, 2, 5, 100],
    'seeds': 10,
}
EXPERIMENTS['reacher-metalearning'] = {
    'train_environments': ['pirl/ReacherGoal-seed{}-0.1-v0'.format(seed) for seed in range(0, 5)],
    'test_environments': ['pirl/ReacherGoal-seed{}-0.1-v0'.format(seed) for seed in range(5, 10)],
    'expert': 'ppo_cts_200k',
    'eval': ['ppo_cts_200k'],
    'irl': ['airl_random_short',
            'airl_so_short',
            'airlp_random_short',
            'airlp_so_separate_short'],
    'train_trajectories': [100],
    'test_trajectories': [1, 5, 10, 100],
}

# Test of RL parallelism
for n in [1, 4, 8, 16]:
    EXPERIMENTS['parallel-cts-easy-{}'.format(n)] = {
        'environments': [
            'Reacher-v2',
            'InvertedPendulum-v2',
            'InvertedDoublePendulum-v2'
        ],
        'parallel_rollouts': n,
        'expert': 'ppo_cts',
        'eval': [],#['ppo_cts'],
        'irl': ['airl_so'],
        'test_trajectories': [1000],
    }
    EXPERIMENTS['parallel-cts-easy-fast-{}'.format(n)] = {
        'environments': [
            'Reacher-v2',
            'InvertedPendulum-v2',
            'InvertedDoublePendulum-v2'
        ],
        'parallel_rollouts': n,
        'expert': 'ppo_cts',
        'eval': [],
        'irl': ['airl_so_dummy'],
        'test_trajectories': [1000],
    }
    EXPERIMENTS['parallel-cts-reacher-{}'.format(n)] = {
        'environments': [
            'Reacher-v2',
        ],
        'parallel_rollouts': n,
        'expert': 'ppo_cts',
        'eval': [],
        'irl': ['airl_so'],
        'test_trajectories': [1000],
    }
    EXPERIMENTS['parallel-cts-reacher-fast-{}'.format(n)] = {
        'environments': [
            'Reacher-v2',
        ],
        'parallel_rollouts': n,
        'expert': 'ppo_cts',
        'eval': [],
        'irl': ['airl_so_dummy'],
        'test_trajectories': [1000],
    }
    EXPERIMENTS['parallel-cts-reacher-fast-rl-{}'.format(n)] = {
        'environments': [
            'Reacher-v2',
        ],
        'parallel_rollouts': n,
        'expert': 'ppo_cts_shortest',
        'eval': [],
        'irl': [],
        'test_trajectories': [10],
    }
    EXPERIMENTS['parallel-cts-humanoid-fast-rl-{}'.format(n)] = {
        'environments': [
            'Humanoid-v2',
        ],
        'parallel_rollouts': n,
        'expert': 'ppo_cts_shortest',
        'eval': [],
        'irl': [],
        'test_trajectories': [10],
    }
